"""Download the public local ASR model once; recordings never go to this service."""
import json
from pathlib import Path

from huggingface_hub import snapshot_download

repository = "Systran/faster-whisper-base"
destination = Path(__file__).resolve().parent.parent / ".run" / "models" / "whisper-base"
revision = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
print("正在准备本机中文语音模型：", revision, flush=True)
snapshot_download(repository, revision=revision, local_dir=destination,
                  allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.*", "preprocessor_config.json"])
(destination / "source.json").write_text(json.dumps({"repository": repository, "revision": revision}, indent=2) + "\n")
print("模型已保存，可启动后端使用语音输入。", flush=True)
