import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import gmail_demo
import speech_api
from speech_api import LocalSpeech, SpeechError, MAX_AUDIO_BYTES


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.model_path = Path(self.folder.name)
        (self.model_path / "model.bin").write_bytes(b"model double")
        for name in ("config.json", "tokenizer.json", "vocabulary.txt"):
            (self.model_path / name).write_text("test fixture")
        self.loads = 0
        self.calls = 0
        self.segments = [SimpleNamespace(text="主题是社区活动，正文是欢迎参加。", no_speech_prob=.1, avg_logprob=-.2)]
        self.audio = [.1] * 16000
        self.encoded = base64.b64encode(b"recorded audio double" * 4).decode()
        self.service = LocalSpeech(self.model_path, loader=self.load, decoder=lambda _: self.audio)

    def tearDown(self):
        self.folder.cleanup()

    def load(self):
        self.loads += 1
        return SimpleNamespace(transcribe=self.transcribe)

    def transcribe(self, audio, **options):
        self.calls += 1
        return iter(self.segments), None

    def test_chinese_result_is_returned_and_model_is_reused(self):
        self.assertEqual(self.service.transcribe(self.encoded)["text"], "主题是社区活动，正文是欢迎参加。")
        self.service.transcribe(self.encoded)
        self.assertEqual(self.loads, 1)
        self.assertEqual(self.calls, 2)

    def test_invalid_or_oversized_recording_never_reaches_model(self):
        for encoded in ["not-base64!", base64.b64encode(b"short").decode(), base64.b64encode(b"a" * (MAX_AUDIO_BYTES + 1)).decode()]:
            with self.subTest(length=len(encoded)), self.assertRaises(SpeechError):
                self.service.transcribe(encoded)
        self.assertEqual(self.loads, 0)

    def test_silence_and_invalid_duration_do_not_create_transcript(self):
        for audio in [[0.] * 16000, [.1] * 3000, [.1] * (16000 * 23 + 1)]:
            self.audio = audio
            with self.assertRaises(SpeechError):
                self.service.transcribe(self.encoded)
        self.assertEqual(self.loads, 0)

    def test_unavailable_model_has_actionable_status(self):
        (self.model_path / "vocabulary.txt").unlink()
        self.assertFalse(self.service.status()["available"])
        (self.model_path / "model.bin").unlink()
        self.assertFalse(self.service.status()["available"])
        with self.assertRaises(SpeechError) as error:
            self.service.transcribe(self.encoded)
        self.assertEqual(error.exception.status, 503)

    def test_concurrent_recording_is_rejected_without_queueing(self):
        self.service.lock.acquire()
        try:
            with self.assertRaises(SpeechError) as error:
                self.service.transcribe(self.encoded)
            self.assertEqual(error.exception.status, 409)
            self.assertEqual(self.loads, 0)
        finally:
            self.service.lock.release()

    def test_no_speech_segment_is_not_accepted_as_user_text(self):
        self.segments = [SimpleNamespace(text="hallucinated words", no_speech_prob=.9, avg_logprob=-.1)]
        with self.assertRaises(SpeechError):
            self.service.transcribe(self.encoded)
        self.assertFalse(self.service.lock.locked())

    def test_decoder_failure_releases_lock_and_allows_a_new_recording(self):
        self.service.decoder = lambda _: (_ for _ in ()).throw(ValueError("invalid audio"))
        with self.assertRaises(SpeechError):
            self.service.transcribe(self.encoded)
        self.assertFalse(self.service.lock.locked())
        self.service.decoder = lambda _: self.audio
        self.assertTrue(self.service.transcribe(self.encoded)["ok"])

    def test_api_validates_audio_and_returns_only_recognized_text(self):
        with patch.object(speech_api, "speech", self.service):
            client = TestClient(gmail_demo.app)
            self.assertTrue(client.get("/speech/status").json()["available"])
            self.assertEqual(client.post("/speech/transcribe", json={}).status_code, 422)
            self.assertEqual(client.post("/speech/transcribe", json={"audio_base64": "!" * 32}).status_code, 422)
            response = client.post("/speech/transcribe", json={"audio_base64": self.encoded})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["text"], "主题是社区活动，正文是欢迎参加。")


if __name__ == "__main__":
    unittest.main()
