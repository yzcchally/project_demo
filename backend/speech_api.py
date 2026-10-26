"""Short microphone clips transcribed on the connected computer, without cloud ASR."""

import base64
import binascii
import importlib.util
import io
import os
import threading
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field

MAX_AUDIO_BYTES = 512 * 1024
SAMPLE_RATE = 16000
DEFAULT_MODEL = Path(__file__).resolve().parent.parent / ".run" / "models" / "whisper-base"


class SpeechError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


class LocalSpeech:
    def __init__(self, model_path=None, loader=None, decoder=None):
        self.model_path = Path(model_path or os.environ.get("SPEECH_MODEL_PATH", str(DEFAULT_MODEL)))
        self.loader, self.decoder = loader, decoder
        self.model = None
        self.lock = threading.Lock()

    def available(self):
        complete = all((self.model_path / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json"))
        complete = complete and any((self.model_path / name).is_file() for name in ("vocabulary.txt", "vocabulary.json"))
        return complete and (
            self.loader is not None or importlib.util.find_spec("faster_whisper") is not None)

    def status(self):
        ready = self.available()
        return {"ok": True, "available": ready, "message": "语音输入已就绪" if ready else "语音尚未就绪，请先连接已配置语音的电脑，或使用文字输入。"}

    def transcribe(self, encoded):
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise SpeechError("录音数据无效，请重新录制") from None
        if not 32 <= len(data) <= MAX_AUDIO_BYTES:
            raise SpeechError("录音大小无效，请录制 20 秒以内的一句话")
        if not self.available():
            raise SpeechError(self.status()["message"], 503)
        if not self.lock.acquire(blocking=False):
            raise SpeechError("上一段语音仍在识别，请稍后再试", 409)
        try:
            try:
                if self.decoder is None:
                    from faster_whisper.audio import decode_audio
                    audio = decode_audio(io.BytesIO(data), sampling_rate=SAMPLE_RATE)
                else:
                    audio = self.decoder(data)
            except Exception:
                raise SpeechError("录音无法读取，请重新录制") from None
            if len(audio) < SAMPLE_RATE * .4:
                raise SpeechError("录音太短，请说完一句话再点击转文字")
            if len(audio) > SAMPLE_RATE * 23:
                raise SpeechError("录音太长，请分成 20 秒以内的短句")
            if max(abs(float(v)) for v in audio) < .01 or sum(float(v) ** 2 for v in audio) / len(audio) < .000001:
                raise SpeechError("这次没有听到声音，请靠近手机麦克风再试")
            try:
                if self.model is None:
                    if self.loader is not None:
                        self.model = self.loader()
                    else:
                        from faster_whisper import WhisperModel
                        self.model = WhisperModel(str(self.model_path), device="cpu", compute_type="int8",
                                                  cpu_threads=4, num_workers=1, local_files_only=True)
                segments, _ = self.model.transcribe(audio, language="zh", beam_size=3,
                    vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
                    condition_on_previous_text=False, initial_prompt="以下是简体中文的邮件需求，包含主题和正文。")
                text = "".join(s.text.strip() for s in segments
                               if getattr(s, "no_speech_prob", 0) < .6 and getattr(s, "avg_logprob", 0) > -1.5).strip()
            except Exception:
                raise SpeechError("本次语音识别未完成，请重试或使用文字输入", 503) from None
            if not text or len(text) > 2000:
                raise SpeechError("这次没有听清，请重试或使用文字输入")
            return {"ok": True, "text": text, "message": "语音已转成文字，请核对识别内容。"}
        finally:
            self.lock.release()


class AudioClip(BaseModel):
    audio_base64: str = Field(min_length=16, max_length=700000)


speech = LocalSpeech()


def register(app):
    @app.get("/speech/status")
    def speech_status():
        return speech.status()

    @app.post("/speech/transcribe")
    def transcribe(clip: AudioClip):
        try:
            return speech.transcribe(clip.audio_base64)
        except SpeechError as error:
            raise HTTPException(error.status, str(error)) from None
