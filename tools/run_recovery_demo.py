"""Run the regular API with one explicit real-phone foreground-loss injection.

Stop the regular backend first. Run with --case composer-background, then use
the existing App/QR/approval flow. The driver presses HOME once, immediately
after observing the first complete Huawei composer. Production recovery must
return to that recorded composer; the driver never resumes, edits or sends it.
"""
import argparse
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def install_injection(device, *, before_inject=None, after_observed=None):
    from device.demo import install_injection as install
    return install(device, before_inject=before_inject, after_observed=after_observed, trigger="cli")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["composer-background"], required=True)
    args = parser.parse_args()
    with socket.socket() as connection:
        connection.settimeout(.3)
        if connection.connect_ex(("127.0.0.1", 8000)) == 0:
            raise SystemExit("8000 端口已有后端，请先在原终端按 Ctrl+C 停止，再启动测试后端。")
    import main as application
    from agent.runtime import get_engine
    import uvicorn

    engine = get_engine()
    if engine.active():
        raise SystemExit("请先完成或取消当前任务，再启动故障注入演示。")
    if not engine.device.__class__.__module__.startswith("device."):
        raise SystemExit("故障注入演示必须使用实际手机适配器。")
    install_injection(engine.device,
                      before_inject=lambda event: engine._demo_fault_registered(event["task_id"], event),
                      after_observed=lambda event: engine._demo_fault_observed(event["task_id"], event))
    print("测试模式：首次写信页将被主动切到桌面一次，随后由正式恢复逻辑继续。", flush=True)
    print("请使用 App 发起新任务；发送仍需用户在 App 核对并确认。", flush=True)
    uvicorn.run(application.app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
