# SilverGuide 2.0 / 小银助手

Android 真机 + USB + 本地电脑的受控任务助手。自然语言入口已接入 LLM、LangGraph、Pydantic、Mobilerun 和 WebSocket；发送授权及结果验证仍由确定性模块掌握。

## 本机启动

```bash
./start_backend.command
```

另一个终端恢复 USB 转发：

```bash
adb reverse tcp:8000 tcp:8000
```

首次配置依赖和语音模型：

```bash
uv pip install --python .venv/bin/python -r backend/requirements-dev.txt
uv pip install --python .venv/bin/python -r backend/requirements-speech.txt
.venv/bin/python tools/prepare_speech.py
./build_android.command assembleDebug testDebugUnitTest lintDebug
./connect_phone.command
```

手机解锁并允许 USB 调试，启用小银助手无障碍服务。健康检查为 `http://127.0.0.1:8000/health`，接口文档为 `/docs`。Windows 可创建 Python 3.12 虚拟环境，用其 `python -m pip` 安装相同 requirements，再运行 `python backend/main.py` 和 `adb reverse`；Android Studio 打开仓库根目录。具体 Mac 路径见 [MAC_SETUP.md](MAC_SETUP.md)。

## 用一句话发起任务

1. 在 App 输入“帮我扫描二维码，把网址发给测试收件人，主题是社区活动”，点“让小银完成这个任务”；也可点“说出您的需求”，录音后点“说完了，转成文字”。
2. LLM 将需求转成结构化任务。未知联系人会要求提供完整邮箱，可勾选记住这个称呼。已保存的称呼在下一次任务中直接解析；不猜测邮箱。
3. App 根据真实任务状态打开扫码器。扫描 [测试二维码](fixtures/share-link-qr.png)，实际网址为 `https://example.org/`。扫码超时可点击“再次打开扫码”。
4. LangGraph 继续打开并核对网页，观察华为邮件写信页。GUI 模型提出受限字段动作，守卫核对控件和内容，再执行并回读完整草稿。
5. 返回 App 检查收件人、主题、正文和任务标记。可修改后重新核对；勾选并点击“确认发送这封邮件”才会尝试发送一次。
6. 后端独立核对已发送记录。收到邮件后可人工确认收件；这与自动发送记录分别保存。UNKNOWN 只提供重新核验，不自动重发。

通知栏持续显示任务进度、暂停/继续和取消，即使浏览器或邮件在前台也能操作。中文进度由后台服务播报；“解释当前步骤”提供基于真实状态的详细说明。“管理联系人称呼”可添加、替换和删除映射。“按字段准备邮件”保留直接填写表单的受控路径。

只说“主题是……，正文是……”时仍可提取到手动表单。明确的自然语言扫码/分享指令才进入 Agent 任务。当前仅适配已测试的华为电子邮件，其他邮箱 App 未完成同等适配。扫码使用 companion 内的 ZXing，和课程文档首选的已有手机扫码器存在路线差异，应核对老师允许的范围。

## 模型、语音和边界

在 Git 忽略的本地 `.env` 配置 `DEEPSEEK_API_KEY`。默认模型沿用本机已验证的 `deepseek-v4-flash-vision-exp`，可用 `SILVERGUIDE_TEXT_MODEL`、`SILVERGUIDE_VISION_MODEL`、`SILVERGUIDE_MODEL_BASE_URL` 替换。模型无权批准或直接发送，不能提交任意 shell。

语音使用 [faster-whisper](https://github.com/SYSTRAN/faster-whisper) 和 [Whisper base 转换模型](https://huggingface.co/Systran/faster-whisper-base)。模型只下载一次，录音通过 USB 交给本机处理，不上传外部 ASR。录音最多 20 秒，支持取消，离开页面停止，临时音频在完成或取消后删除。华为系统识别曾未返回文字，因此采用了这条已验证的替代路线。

Mobilerun 使用 `local-android-adb`，可利用已配置的 Portal；默认无需云手机。必要时用 `SILVERGUIDE_DEVICE_BACKEND=adb` 选择原生适配回退。单手机、单活动任务；后端仅监听回环地址，并拒绝外部网页 Origin 读取私有任务 WebSocket。

## 测试和证据

```bash
.venv/bin/python -m pytest backend -q
./build_android.command assembleDebug testDebugUnitTest lintDebug
.venv/bin/python tools/evaluate.py
.venv/bin/python tools/prune_evidence.py
```

任务、单次发送记录、联系人、LangGraph 检查点及执行日志保存在 `.run/workflows/`。API 使用 `task_id + draft_digest + approval_nonce` 绑定批准；先记录发送尝试，再点击发送。重启不会自动重放外部发送。相同请求键可以安全重复提交，已尝试的相同邮件受到防重复保护。

本地证据可能包含测试邮箱和草稿；不会提交到 Git。清理工具默认仅预览 30 天前的终态屏幕文件，显式 `--apply` 才删除；保留任务和幂等记录。当前实测结果见 [ACCEPTANCE.md](ACCEPTANCE.md)，不能把单元测试当成正式 36 轮真机实验或老年人研究。

更多资料：[架构](ARCHITECTURE.md)、[API](API_CONTRACT.md)、[演示与评估](DEMO_AND_EVALUATION.md)、[AI 使用与团队贡献](AI_USAGE_AND_CONTRIBUTIONS.md)、[英文实现报告](IMPLEMENTATION_REPORT_EN.md)。
