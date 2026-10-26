# Mac + 安卓真机运行

本地项目目录：`/Users/pytenter/Documents/临时项目1/project_demo`。

当前入口是小银助手的受控邮件任务，使用方法见 [README.md](README.md)，验证范围见 [ACCEPTANCE.md](ACCEPTANCE.md)。下面的 10 月 20 日、25 日记录属于历史检查，不代表当前服务或发信状态。

## 已配置的环境

- 项目独立 Python 3.12 环境：`.venv`；不修改系统 Python。
- Android SDK：`~/Library/Android/sdk`。
- OpenJDK 25：`~/Library/Java/JavaVirtualMachines/project-demo-jdk-25.jdk`。
- 构建使用仓库提供的 Gradle Wrapper。
- 后端直接依赖固定在 `backend/requirements.txt`，测试与语音依赖分别位于 `requirements-dev.txt` 和 `requirements-speech.txt`；本地完整安装快照不纳入 Git。

后续可用 macOS Apple Silicon 版 Android Studio 打开本目录继续开发；命令行脚本也可以独立构建、安装和启动。

## 当前版本 2.0（2026-10-26）

- 华为邮件中文草稿填写与完整回读已在真机通过，默认签名可替换。
- 用户在 App 审阅并批准后，学校邮箱发出一封测试邮件；后端独立核对到匹配的已发送记录，用户也在 Gmail 核对收到，主题与正文正确。
- 真实相机扫描已返回 `https://example.org/`；完整扫码分享任务的验收结果见 [ACCEPTANCE.md](ACCEPTANCE.md)。
- 当前使用 LangGraph + SQLite checkpoint、FastAPI/Pydantic、Mobilerun 本地 ADB、WebSocket 及 Android 前台通知服务。App 支持自然语言任务、联系人澄清、真实扫码和发送前编辑核对。
- 批准绑定草稿摘要及新 nonce；发送次数持久化后才点击发送，结果未知仅允许重新核验。后台通知支持暂停、继续和取消。
- 70 项 Python 测试及 13 个子测试、4 项 Android JVM 测试通过；Debug 构建与 lint 通过（0 errors，仍有警告）。旧版发信和新版 Agent 的实际验收分开记录于 [ACCEPTANCE.md](ACCEPTANCE.md)。
- 本机已安装本地语音识别依赖并下载模型；手机录音通过 USB 送入本机转文字，替代当前华为不返回结果的系统识别服务。新电脑的语音配置见 [README.md](README.md)。
- 原生 Gmail 直接发送入口已停用。QQ 邮箱不属于当前受控邮件适配范围。

## 历史真机验证（2026-10-20）

- 华为 Android 12 真机安装、USB 转发、后端通信和无障碍服务正常。
- 实测项目代码可返回桌面、输入中文。
- 实测从 App 提交“打开手机的设置，打开后结束任务。”：AI 返回桌面、下拉搜索、点击设置，随后返回 `done` 并结束。最终手机停留在系统设置，后端状态为 `completed`。
- 10 项 Python 回归测试通过，包括完成后结束、停止后不执行模型晚到的指令，以及 QQ 邮箱正文替换和重复输入保护。
- 手机没有原生 Gmail，固定流程会在操作前给出缺少 Gmail 的提示；真实收件人已在本地配置。
- 已使用 QQ 邮箱测试通用 AI 流程：核对收件人为 `school-account@example.edu`、主题和正文均为 `demo`，点击发送一次。随后 QQ 邮箱提示“对方服务器连接失败”，其诊断显示手机正常联网、连接邮箱服务器失败；“已发送”未确认这封邮件，草稿箱为空。因此本次发信结果仍未确认，不能认定已发出或送达，也没有重复发送。
- 已修复 QQ 富文本正文对无障碍写入报告成功、实际为空的问题。本次单行 ASCII 正文通过键盘输入后读取界面核对；中文或多行 QQ 正文尚未验证。
- 测试结束已停止 AI，并恢复手机原来的 USB 常亮设置。

本地验收记录位于 `.run/verification.json`，本次模型执行日志另存于 `.run/settings-task.log`。
QQ 邮箱的发送阶段日志为 `.run/qqmail-send.log`，字段核对和发送结果分别在 `.run/qqmail-fields-verified.json`、`.run/qqmail-send-result.json`。

## 历史学校邮箱检查（2026-10-25，开发前）

- 华为自带“电子邮件”已配置学校邮箱 `school-account@example.edu`。重新打开并刷新后，网络错误提示消失，收件箱加载出了较新的邮件。
- 自动同步仍处于关闭状态；本次没有改变该设置。
- 已检查空白写信页：收件人、主题、正文和发送控件可从界面树读取。正文默认带有“发自我的手机”签名，后续适配需核对完整正文。
- 本次没有填写或发送新邮件；上次 QQ 邮箱测试邮件的收件结果仍未确认。写信控件可读取不代表自动填写和发信已经通过验证。
- 后端与 USB 端口转发已恢复；当前 Demo 无障碍服务关闭，自动执行前需要在手机设置中重新开启。
- 现有 10 项 Python 回归测试通过。本次检查记录在 `.run/schoolmail-check-2026-10-25.json`。

## 邮箱适配范围

当前受控流程适配华为自带“电子邮件”的学校邮箱。QQ 邮箱历史连接失败的测试没有确认送达，不能与新版华为邮件的成功验收混为一谈。Outlook、Gmail 安卓客户端尚未实现同等的草稿回读与发送记录核验。

## 启动后端

双击根目录的 `start_backend.command`，或在终端运行：

```bash
cd '/Users/pytenter/Documents/临时项目1/project_demo'
./start_backend.command
```

健康检查：<http://127.0.0.1:8000/health>。
接口文档：<http://127.0.0.1:8000/docs>。

## 编译并连接手机

1. 打开安卓手机的开发者选项和 USB 调试。
2. 使用数据线连接 Mac，在手机弹窗中允许调试。
3. 运行 `./build_android.command` 编译 APK。
4. 运行 `./connect_phone.command` 安装 App，并设置 USB 端口转发。
5. 在 App 的“连接与基础控制”中点“打开无障碍设置”，启用 Demo。
6. 回到 App，点“检查手机连接”。应显示“手机已连接；无障碍服务已开启”。

真机通过 `adb reverse tcp:8000 tcp:8000` 使用手机的 `127.0.0.1:8000` 访问 Mac 后端。重新插拔手机或重启设备后，再运行一次 `connect_phone.command`。

## 模型配置与任务入口

本地 `.env` 已创建，不会提交到 Git。填写：

```dotenv
DEEPSEEK_API_KEY=你的密钥
```

保存后重启后端，让配置生效。密钥只在本地保存。

- “检查手机连接”不需要模型密钥，也不会执行邮件流程。
- 开发用基础 AI 操作需要密钥，以及账号可用的视觉模型。
- 可先测试默认任务“打开手机的设置，打开后结束任务。”。AI 观察到任务完成后会返回 `done` 并结束。
- “查看 AI 任务状态”可以查看是否完成；“停止 AI 执行”会取消后续操作，即使模型还在返回结果也不会再执行该结果。
- “让小银完成这个任务”调用模型理解需求，并由实际图流程管理澄清、扫码和批准。视觉模型只建议受限的准备动作，独立守卫核对控件和内容。
- “按字段准备邮件”保留确定性表单入口；两种入口都需要预览、用户明确确认和独立发送结果核验。
- 旧 Gmail 直接发送接口已停用；通用 AI 禁止在已识别的邮件 App 中点击或输入。

本机后台启动的服务进程号保存在 `.run/backend.pid`，日志在 `.run/backend.log`。重启前先查看任务状态，确认没有进行中的任务；核对 PID 确属本项目后停止，避免重复占用 8000 端口。任务数据库和证据位于 `.run/workflows/`，重启不会删除发送尝试记录，也不会自动重发。

## 与 Windows 队友协作

ADB 会优先读取 `ADB_PATH`、系统 PATH、`ANDROID_HOME` / `ANDROID_SDK_ROOT`，之后查找各系统的默认 SDK 目录。Windows 原有的 `LOCALAPPDATA/Android/Sdk/platform-tools/adb.exe` 仍受支持。

Windows 也可以运行 `adb reverse tcp:8000 tcp:8000` 使用相同的真机地址，后端仍可按原方式启动：

```powershell
python backend/main.py
```

配置、虚拟环境、SDK 本地路径和构建输出均不提交到 Git。
