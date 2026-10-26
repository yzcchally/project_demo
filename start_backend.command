#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "缺少 Python 环境，请先按 docs/MAC_SETUP.md 配置。"
  exit 1
fi
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
export PATH="$ANDROID_HOME/platform-tools:$PATH"
echo "启动后端：http://127.0.0.1:8000/docs"
exec .venv/bin/python backend/main.py
