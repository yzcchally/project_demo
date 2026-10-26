import os
import subprocess
from fastapi import Body, FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware


def create_app():
    import gmail_demo as legacy
    import recognize
    from workflow_api import register, START_LOCK
    from workflow import get_engine
    from speech_api import register as register_speech
    app = FastAPI(title="SilverGuide Agent API", version="2.0.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.get("/health")
    def health():
        return {**legacy._message(True, "后端连接成功"), "model_configured": bool(os.environ.get("DEEPSEEK_API_KEY", "").strip()),
                "version": "2.0.0", "stack": {"orchestrator": "langgraph", "progress": "websocket", "device": "mobilerun-local-adb", "speech": "local-whisper"}}

    @app.post("/device/check")
    def check_device():
        try:
            adb = legacy._adb()
            result = subprocess.run([adb, "get-state"], capture_output=True, text=True, timeout=15)
            if result.returncode or result.stdout.strip() != "device":
                return legacy._message(False, "手机未就绪：" + (result.stderr or result.stdout).strip())
            enabled = legacy._accessibility_enabled(adb)
            return {**legacy._message(True, "手机已连接；" + ("无障碍服务已开启" if enabled else "请开启小银助手无障碍服务")), "accessibility_enabled": enabled}
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            return legacy._message(False, str(error))

    @app.post("/gmail/start")
    def disabled_legacy_send():
        return legacy._message(False, "直接发送已停用，请使用统一任务入口并核对草稿")

    @app.post("/recognize/start")
    def start_recognize(body: dict = Body()):
        with START_LOCK:
            if get_engine().active():
                return legacy._message(False, "请先完成或取消当前任务")
            return recognize.start(legacy._dump_screen_xml, str(body.get("prompt", "")))

    @app.get("/recognize/status")
    def recognize_status():
        return recognize.status()

    @app.post("/recognize/stop")
    def stop_recognize():
        return recognize.stop()

    register(app)
    register_speech(app)
    return app
