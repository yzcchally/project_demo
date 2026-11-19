"""Android application ID and the stable Kotlin component namespace."""

import os
import re


ANDROID_NAMESPACE = "com.example.demo"


def android_package() -> str:
    package = os.environ.get("SILVERGUIDE_ANDROID_PACKAGE", "").strip() or ANDROID_NAMESPACE
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", package):
        raise RuntimeError("SILVERGUIDE_ANDROID_PACKAGE 必须是有效的 Android 应用包名")
    return package


def android_component(name: str) -> str:
    package = android_package()
    class_name = f".{name}" if package == ANDROID_NAMESPACE else f"{ANDROID_NAMESPACE}.{name}"
    return f"{package}/{class_name}"


def a11y_command() -> tuple[str, str]:
    return android_component("A11yCommandReceiver"), f"{android_package()}.A11Y_COMMAND"
