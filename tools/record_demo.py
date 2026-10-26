"""Record a local Android demo; creates no tasks and never approves a send."""
import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from gmail_demo import _adb

parser = argparse.ArgumentParser()
parser.add_argument("--seconds", type=int, default=150)
args = parser.parse_args()
if not 120 <= args.seconds <= 180:
    parser.error("Use 120–180 seconds for the course demo.")
name = "silverguide-demo-" + uuid.uuid4().hex[:12] + ".mp4"
remote = "/sdcard/Download/" + name
folder = ROOT / ".run" / "demos"
folder.mkdir(parents=True, exist_ok=True)
destination = folder / name
print("Recording the connected phone; use only the test workflow.", flush=True)
probe = subprocess.run([_adb(), "shell", "which", "screenrecord"], capture_output=True)
if probe.returncode == 0 and probe.stdout.strip():
    subprocess.run([_adb(), "shell", "screenrecord", "--time-limit", str(args.seconds), "--bit-rate", "2000000", "--size", "720x1584", remote], check=True)
    subprocess.run([_adb(), "pull", remote, str(destination)], check=True, capture_output=True)
    subprocess.run([_adb(), "shell", "rm", remote], check=True)
    method = "native_screenrecord"
else:
    import io
    import time
    from fractions import Fraction
    import av
    method = "adb_screenshot_video_1fps"
    output = av.open(str(destination), "w")
    try:
        try:
            av.CodecContext.create("libx264", "w")
            codec = "libx264"
        except Exception:
            codec = "mpeg4"
        stream = output.add_stream(codec, rate=1)
        stream.width = 720; stream.height = 1584; stream.pix_fmt = "yuv420p"
        stream.bit_rate = 2000000
        started = time.monotonic()
        frames = 0
        for index in range(args.seconds):
            delay = started + index - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            png = subprocess.run([_adb(), "exec-out", "screencap", "-p"], capture_output=True, check=True, timeout=15).stdout
            with av.open(io.BytesIO(png)) as image:
                frame = next(image.decode(video=0)).reformat(width=720, height=1584, format="yuv420p")
            frame.pts = index; frame.time_base = Fraction(1, 1)
            for packet in stream.encode(frame):
                output.mux(packet)
            frames += 1
        for packet in stream.encode():
            output.mux(packet)
    finally:
        output.close()
metadata = {"video": str(destination), "requested_seconds": args.seconds, "method": method}
destination.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")
print(json.dumps(metadata), flush=True)
