#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
export PATH="$ANDROID_HOME/platform-tools:$PATH"
if ! command -v adb >/dev/null 2>&1; then
  echo "没有找到 ADB，请先安装 Android platform-tools。"
  exit 1
fi
adb devices -l
if ! adb get-state >/dev/null 2>&1; then
  echo "请连接一台安卓手机、打开 USB 调试，并在手机上允许调试授权。"
  exit 1
fi
adb reverse tcp:8000 tcp:8000
apk="app/build/outputs/apk/debug/app-debug.apk"
if [ ! -f "$apk" ]; then
  echo "尚未编译 APK，请先运行 build_android.command。"
  exit 1
fi
adb install -r "$apk"
adb shell am start -n com.example.demo/.MainActivity
echo "App 已打开。请在 App 中开启无障碍服务，然后点“检查手机连接”。"
