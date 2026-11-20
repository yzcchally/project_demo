"""Stage the pinned Android Sherpa runtime and Mandarin recognition assets."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen


STAGE = Path(__file__).resolve().parents[1] / ".run" / "phone-voice"
ASSETS = Path("assets") / "phone-voice"
RELEASE = "v2.0.2"
AAR = "sherpa-onnx-2.0.2.aar"
MODEL_REPO = "csukuangfj/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01"
MODEL_REVISION = "763e4e5169ede1a08c65ee03b20ee6adb797ad0a"
ARTIFACTS = {
    Path(AAR): (
        52_321_252,
        "6420cfb86c2ef8462dadd77b2202b7430487bdfb2bc715e9ce2864871209a80e",
        (
            f"https://github.com/k2-fsa/sherpa-onnx/releases/download/{RELEASE}/{AAR}",
            "https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/assets/766997436?download=1",
        ),
    ),
    ASSETS / "model.int8.onnx": (
        26_342_340,
        "68c9c943840f7d9cf3e8a4970ba50f404feb5277f611fa82b7e72267786fa84a",
        (f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/model.int8.onnx",),
    ),
    ASSETS / "tokens.txt": (
        13_366,
        "6fed8c6c248516f38e7faa19404b57413e8ce259f1cbc1fa4aebc86eac32fdfd",
        (f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/tokens.txt",),
    ),
    ASSETS / "sherpa-onnx-LICENSE.txt": (
        11_358,
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
        (f"https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/{RELEASE}/LICENSE",),
    ),
}


def verify(path: Path, size: int, digest: str) -> bool:
    if not path.is_file() or path.stat().st_size != size:
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == digest


def stage(relative: Path) -> None:
    size, digest, urls = ARTIFACTS[relative]
    destination = STAGE / relative
    if verify(destination, size, digest):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    with tempfile.TemporaryDirectory(dir=destination.parent) as folder:
        partial = Path(folder) / destination.name
        for url in urls:
            request = Request(
                url,
                headers={
                    "Accept": "application/octet-stream",
                    "User-Agent": "SilverGuide-phone-voice-build",
                },
            )
            try:
                with urlopen(request, timeout=45) as response, partial.open("wb") as output:
                    remaining = size
                    while remaining:
                        data = response.read(min(remaining, 1024 * 1024))
                        if not data:
                            raise ValueError("download is incomplete")
                        output.write(data)
                        remaining -= len(data)
                    if response.read(1):
                        raise ValueError("download exceeds the pinned size")
                if not verify(partial, size, digest):
                    raise ValueError("SHA-256 does not match")
                partial.replace(destination)
                return
            except (OSError, ValueError) as error:
                errors.append(f"{url}: {error}")
    raise ValueError(f"Could not stage {relative}\n" + "\n".join(errors))


def main() -> None:
    with ThreadPoolExecutor(max_workers=3) as pool:
        for artifact, _ in zip(ARTIFACTS, pool.map(stage, ARTIFACTS), strict=True):
            print(f"Verified {STAGE / artifact}", flush=True)


if __name__ == "__main__":
    main()
