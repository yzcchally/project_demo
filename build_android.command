#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
if [ -z "${JAVA_HOME:-}" ] && [ -d "$HOME/Library/Java/JavaVirtualMachines/project-demo-jdk-25.jdk/Contents/Home" ]; then
  export JAVA_HOME="$HOME/Library/Java/JavaVirtualMachines/project-demo-jdk-25.jdk/Contents/Home"
fi
if [ -n "${JAVA_HOME:-}" ]; then
  export PATH="$JAVA_HOME/bin:$PATH"
fi
if [ "$#" -eq 0 ]; then
  set -- assembleDebug
fi
exec ./gradlew --console=plain "$@"
