"""Stage the pinned llama.cpp Android CPU runtime for the standalone APK."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path


BUILD_DIR = Path(__file__).resolve().parents[1] / ".run" / "standalone-build"
RELEASE = "llama-b12556"
ARCHIVE_NAME = f"{RELEASE}-bin-android-arm64.tar.gz"
ARCHIVE_SHA256 = "bec4c0392ac329b49e1bebe0d2020b7b80c861697435df6a6355075f3a0dc022"
ARCHIVE_URLS = (
    f"https://github.com/ggml-org/llama.cpp/releases/download/b12556/{ARCHIVE_NAME}",
    "https://api.github.com/repos/ggml-org/llama.cpp/releases/assets/769634958?download=1",
)
NATIVE_DIR = Path("jniLibs") / "arm64-v8a"
LIBRARIES = (
    "libllama-server-impl.so",
    "libllama-common.so",
    "libmtmd.so",
    "libllama.so",
    "libggml.so",
    "libggml-base.so",
    # Kirin 990 supports dot product and FP16 vector arithmetic. Also include
    # the dot-product-only variant and the baseline for older ARM64 CPUs.
    "libggml-cpu-android_armv8.2_2.so",
    "libggml-cpu-android_armv8.2_1.so",
    "libggml-cpu-android_armv8.0_1.so",
)
RUNTIME_FILES = {name: NATIVE_DIR / name for name in LIBRARIES} | {
    "llama-server": NATIVE_DIR / "libsilverguide_llm.so",
    "LICENSE": Path("runtime-assets") / "llama.cpp-LICENSE.txt",
}


def verify_archive(archive: Path) -> None:
    with archive.open("rb") as data:
        digest = hashlib.file_digest(data, "sha256").hexdigest()
    if digest != ARCHIVE_SHA256:
        raise ValueError(f"SHA-256 mismatch for {archive}: {digest}")


def download_archive(archive: Path) -> None:
    headers = {
        "Accept": "application/octet-stream",
        "User-Agent": "SilverGuide-standalone-build",
    }
    with tempfile.TemporaryDirectory(dir=archive.parent) as temp_dir:
        candidate = Path(temp_dir) / ARCHIVE_NAME
        failures = []
        for url in ARCHIVE_URLS:
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    with candidate.open("wb") as output:
                        shutil.copyfileobj(response, output, length=1024 * 1024)
                verify_archive(candidate)
            except (OSError, ValueError) as error:
                failures.append(f"{url}: {error}")
                continue
            candidate.replace(archive)
            return
    raise ValueError("Could not download the verified runtime:\n" + "\n".join(failures))


def get_archive(local_archive: Path | None) -> Path:
    if local_archive is not None:
        verify_archive(local_archive)
        return local_archive
    archive = BUILD_DIR / ARCHIVE_NAME
    try:
        verify_archive(archive)
    except (FileNotFoundError, ValueError):
        download_archive(archive)
    return archive


def stage_runtime(archive: Path, staging: Path) -> None:
    expected = {f"{RELEASE}/{name}": output for name, output in RUNTIME_FILES.items()}
    with tarfile.open(archive, "r|gz") as runtime:
        for member in runtime:
            if member.name not in expected:
                continue
            if not member.isfile():
                raise ValueError(f"Expected a regular file in the runtime: {member.name}")
            output_path = staging / expected.pop(member.name)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with runtime.extractfile(member) as data, output_path.open("wb") as output:
                shutil.copyfileobj(data, output, length=1024 * 1024)
    if expected:
        raise ValueError(f"Runtime archive is missing: {', '.join(expected)}")

    strip = shutil.which("llvm-strip")
    if strip:
        for native_file in (staging / NATIVE_DIR).iterdir():
            subprocess.run([strip, "--strip-unneeded", str(native_file)], check=True)

    for output_path in RUNTIME_FILES.values():
        destination = BUILD_DIR / output_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        (staging / output_path).replace(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use a local copy of the pinned archive.")
    args = parser.parse_args()
    try:
        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        archive = get_archive(args.archive)
        with tempfile.TemporaryDirectory(dir=BUILD_DIR) as temp_dir:
            stage_runtime(archive, Path(temp_dir))
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
    print(f"Staged Android CPU runtime in {BUILD_DIR / NATIVE_DIR}")


if __name__ == "__main__":
    main()
