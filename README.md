# SilverGuide / 小银助手

**An Explainable Mobile GUI Agent for Older Adults**

[![SilverGuide checks](https://github.com/yzcchally/project_demo/actions/workflows/ci.yml/badge.svg)](https://github.com/yzcchally/project_demo/actions/workflows/ci.yml)

SilverGuide 是一个面向老年人操作场景的 Android 跨 App 任务助手原型。用户用文字或中文语音描述目标，系统理解需求并澄清缺失信息。现有主链路为 **扫码 → 打开并核对网页 → 准备邮件 → 用户确认 → 尝试发送一次 → 独立核验结果**；新增 **小银 → 高德查询公交／地铁 → 读取真实方案详情 → 返回小银分步讲解与朗读**。

当前部署方式为 **本地电脑后端 + USB 连接的安卓手机 + 云端语言／视觉模型 API**。手机负责交互、扫码、录音和无障碍操作；电脑负责工作流、模型调用、设备控制和证据记录。既有扫码邮件主链路已经在 Mac 与华为真机上验收；新增高德功能的实际范围单独记录在 [高德验收记录](docs/AMAP_ACCEPTANCE.md)。整体仍是针对指定应用适配的受控原型。

本文更新于 2026-11-12，包含高德公交地铁路线、有界异常恢复与 App 内置演示案例。详细实测范围见 [验收记录](docs/ACCEPTANCE.md)，课程实验方案见 [演示与评估](docs/DEMO_AND_EVALUATION.md)。

## 目录

- [当前功能与支持范围](#当前功能与支持范围)
- [整体架构](#整体架构)
- [技术栈](#技术栈)
- [核心功能如何实现](#核心功能如何实现)
- [模型与程序的职责](#模型与程序的职责)
- [代码结构](#代码结构)
- [安装与运行](#安装与运行)
- [完整演示案例](#完整演示案例)
- [后端接口](#后端接口)
- [测试与验收](#测试与验收)
- [数据与隐私](#数据与隐私)
- [常见问题与后续工作](#常见问题与后续工作)
- [更多文档](#更多文档)

## 当前功能与支持范围

| 能力 | 当前实现 | 验证范围 |
| --- | --- | --- |
| 自然语言任务 | 结构化理解任务类型、收件人／称呼、主题、正文和网址；不支持的目标明确停止 | 已做真实模型调用和小样本检查 |
| 高德公交地铁 | 明确起终点、城市和偏好；真实 GUI 查询与详情读取，大字分段卡片、中文朗读和当前路线追问 | 代码与独立测试已接入；实际真机结论见 [高德验收记录](docs/AMAP_ACCEPTANCE.md) |
| 地点澄清 | 从高德“我的位置”读取真实地址和城市，并核对授权、精度及定位新鲜度；失败时填写起点；同名地点绑定本次候选选择 | 以路线查询标识和选择 nonce 隔离旧回包 |
| 联系人澄清 | 未知称呼要求用户提供完整邮箱，可保存、修改、删除称呼映射；多个可能收件人要求澄清 | 已接入 App、后端和持久化 |
| 真实扫码 | App 内相机扫描二维码，实际结果与当前扫码轮次绑定 | 真机完成扫描、暂停、继续和取消 |
| 网页核对 | 打开华为浏览器，读取原生完整地址，与目标 HTTPS URL 比较 | 主链路及当前网页地址读取通过 |
| 华为邮件草稿 | 填写并回读收件人、中文主题、完整正文和网址，处理默认签名 | 已完成真实草稿与发信验收 |
| 发送前确认 | App 展示完整草稿，可编辑；用户勾选核对并确认后才允许发送 | 真机完成，旧批准失效等边界有回归覆盖 |
| 结果核验 | 单次发送后查找匹配的已发送详情；另外保存人工收件确认 | 已发送核验与用户收件确认均通过 |
| 进度与控制 | WebSocket、后台前台服务、通知栏暂停／继续／取消、事件回放 | 通知控制和扫码控制已做真机检查 |
| 中文语音 | 手机短录音 → 电脑本地 Whisper 转文字；手机 TTS 播报进度 | 用户实际确认过中文输入和播报 |
| 持久化与评估 | SQLite 任务与检查点、动作／事件证据、试运行报告、基础反馈对照模式 | 自动化回归及本机试运行记录 |

后端定义了五类支持目标：

| 任务类型 | 用户目标 | 当前验收边界 |
| --- | --- | --- |
| `scan_and_share` | 扫描二维码，把网页链接通过邮件分享 | 已完成真实端到端流程 |
| `share_url` | 分享需求中明确给出的网址 | 已实现，尚未完成独立的系统性多轮验收 |
| `share_current_page` | 读取并分享当前浏览器网页 | 原生完整地址读取已做真机检查；独立发信场景仍需补充多轮验收 |
| `open_url` | 扫码或提供网址，只打开并核对网页 | 已实现；网页操作和扫码控制已有真机证据 |
| `plan_transit` | 查询同城公交／地铁，回小银分步讲解 | 独立于邮件预检、批准和收件；结果与限制见高德专用验收记录 |

**扫码邮件链路使用华为浏览器与华为自带“电子邮件”两个外部应用；路线链路使用高德地图。** 扫码器位于小银助手内部。路线任务不要求邮箱账号、不发送邮件，也不启动行中导航。Outlook、QQ 邮箱及原生 Gmail 安卓客户端尚未完成同等适配；电脑 Chrome 中的 Gmail 只用于人工核对收件。发信通过手机邮件客户端完成，没有接入 Gmail 发信 API 或直接 SMTP 发信。

## 整体架构

```mermaid
flowchart TB
    subgraph phone["Android 真机"]
        UI["小银助手：输入、澄清、草稿确认、路线步骤、进度"]
        QR["ZXing 相机扫码"]
        Voice["MediaRecorder 录音 / TextToSpeech 播报"]
        Service["前台服务 / WebSocket / 通知控制"]
        A11y["无障碍服务：控件读取与文本输入"]
        Apps["华为浏览器 / 华为电子邮件 / 高德地图"]
    end
    subgraph computer["本地电脑后端"]
        API["FastAPI / Pydantic"]
        Graph["LangGraph：理解、澄清、邮件分支 / 路线查询分支"]
        Guard["动作守卫 / 草稿摘要 / 批准校验 / 单次发送记录"]
        Device["Mobilerun local-android-adb / ADB / 华为与高德适配器"]
        Store["SQLite 任务与检查点 / JSONL 证据"]
        ASR["本地 faster-whisper"]
    end
    Model["云端模型 API：文本理解 + 截图与控件候选识别"]
    UI --> QR
    UI --> Voice
    UI <--> Service
    Service <-->|"USB adb reverse：HTTP / WebSocket"| API
    Voice -->|"短录音经本地 API"| ASR
    API --> Graph
    Graph <--> Model
    Graph <--> Store
    Graph --> Guard
    Guard --> Device
    Device <-->|"USB 调试"| A11y
    Device <-->|"启动、观察、点击、核验"| Apps
    A11y <--> Apps
```

手机请求 `127.0.0.1:8000`，由 `adb reverse` 转发到电脑后端。后端监听电脑回环地址，使用本机 USB 设备适配器操作手机。自然语言理解和视觉判断需要所配置模型服务的网络连接；可选语音识别模型下载后在电脑本地运行。

## 技术栈

版本以仓库依赖声明为准：[Android 依赖](gradle/libs.versions.toml)、[Android 构建](app/build.gradle.kts)、[Python 依赖](backend/requirements.txt)、[语音依赖](backend/requirements-speech.txt)。

| 层次 | 技术与当前版本 | 在项目中的职责 |
| --- | --- | --- |
| Android UI | Kotlin 2.2.10、Jetpack Compose BOM 2026.02.01、Material 3 | 任务输入、联系人、草稿确认、大字操作和进度界面 |
| Android 生命周期 | Activity Compose 1.13.0、Lifecycle 2.11.0、Core KTX 1.19.1 | 页面恢复、录音生命周期、后台任务连接 |
| Android 网络 | OkHttp 4.12.0；常规任务 HTTP 使用 `HttpURLConnection` | WebSocket、通知控制请求、任务 API |
| 扫码 | JourneyApps / ZXing Embedded 4.3.0 | 内置相机扫码 Activity 与真实二维码结果 |
| 手机语音 | Android `MediaRecorder`、`TextToSpeech` | AAC/M4A 短录音、中文步骤播报 |
| Android 构建 | Gradle 9.6.0、AGP 9.4.1、构建 JDK 25 | APK、JVM 测试与 lint；Java 源码兼容级别为 11 |
| Android 平台 | `compileSdk/targetSdk=37`、`minSdk=24` | 当前 SDK 包名为 `platforms;android-37.0`；实测手机为 Android 12 / API 31 |
| 后端运行时 | Python 3.12、FastAPI 0.153.0、Uvicorn 0.54.0 | HTTP、WebSocket 和本地服务入口 |
| 数据契约 | Pydantic 2.14.1 | 任务、模型输出、控制请求与字段校验 |
| 工作流 | LangGraph 1.2.17、SQLite checkpointer 3.1.2 | 图执行、人机交互中断、恢复与检查点 |
| 模型调用 | OpenAI 兼容 Python SDK `openai==3.35.1`，默认连接 DeepSeek | 文本任务理解、图像与界面候选识别；使用 SDK 不代表调用 OpenAI 服务 |
| 设备控制 | `mobilerun-core[local]==1.10.1`、`mobilerun-core-local==0.7.0`、ADB | `local-android-adb` 观察和操作，结合华为应用专用适配 |
| 高德截图 OCR | macOS Apple Vision、`tools/amap_ocr.m` | 当已观察路线按钮没有可用 UI 树边界时，读取当前截图并再次确认边界；不读取路线 REST API |
| 本地 ASR | `faster-whisper==1.2.1`、`av==16.1.0`、Whisper base | CPU / int8 中文转写，无需外部 ASR 服务 |
| 数据与日志 | SQLite、JSONL、Android SharedPreferences | 任务、联系人、发送记录、检查点、事件游标和本地界面证据 |
| 测试与 CI | pytest 9.1.1、pytest-asyncio 1.4.1、httpx 0.28.1、JUnit、GitHub Actions | 后端回归、Android JVM 测试、Debug 构建与 lint |

## 核心功能如何实现

### 1. 从一句需求进入可恢复工作流

App 将原始需求提交给后端，模型返回经过 Pydantic 校验的 `TaskIntent`。程序再核对用户明确给出的邮箱、网址和已保存联系人：未知称呼、缺少必需字段或存在多个收件人时，进入澄清页面，不让模型猜地址。联系人称呼与邮箱的关联只有用户明确保存后才生效。

工作流使用真正的 `StateGraph`，在澄清、扫码和批准处调用 `interrupt()`，收到有效输入后用 `Command(resume=...)` 恢复；图检查点保存在 SQLite。下图为网页／邮件分支，`open_url` 在网页核对完成后结束；`plan_transit` 进入独立路线节点，不经过邮件预检、草稿、批准或发送：

```mermaid
flowchart LR
    U["理解需求"] --> Q{"信息完整？"}
    Q -->|"否"| C["中断：用户澄清"]
    C --> Q
    Q -->|"是"| S["按需中断：真实扫码"]
    S --> P["参数与网址预检"]
    P --> B["打开网页并读取完整地址"]
    B -->|"只打开网页"| E["记录结果"]
    B -->|"需要分享"| D["准备并回读草稿"]
    D --> A["中断：用户预览与批准"]
    A --> R["恢复原草稿并重新核对"]
    R --> T["持久化发送尝试，再点击一次"]
    T --> V["核验已发送记录"]
    V --> E
```

### 2. 真实扫码与完整网址核对

内置 ZXing 扫码器将实际相机结果连同 `scan_nonce` 交回后端。暂停、继续或重新扫码会管理相机生命周期及轮次；旧轮次的回调不能覆盖当前结果。扫码界面有超时，可重新打开。

网址校验只接受符合约束的 HTTPS 公网域名形式，拒绝 IP、localhost／本地域名、嵌入账号密码及非默认端口等。随后通过 Android Intent 打开浏览器，读取华为浏览器原生完整地址，包括必要时展开隐藏地址栏。网页正文中的链接、仅显示域名的控件或其他应用的文字不能作为完整地址证据；跳转后地址不符则停止核对。

这是输入格式和页面一致性检查，尚未实现完整的 DNS、网站信誉或恶意网页检测。

### 3. 截图与界面树共同识别

后端通过设备适配器观察手机，取得截图与 Android UI 树。感知层从当前写信页提取收件人、主题、正文候选，附上资源 ID、角色、边界、文字和内容描述，为候选生成当前观察有效的 `target_id`。

默认 Mobilerun 路径的视觉请求同时提供 **PNG 截图、界面候选和预期草稿**；原生 ADB 回退路径当前不附截图，只提交结构化观察。模型返回页面类别及填写建议；动作守卫逐项核对候选是否存在、字段角色是否一致、输入是否等于当前草稿。可执行建议限制为 `enter_text`、`tap_element`、`wait`，模型不能输出任意坐标或 shell，也不能批准或发送邮件。无效建议最多触发一次重新观察与提议，再不满足约束就停止。

因此，界面识别结合了多模态模型判断、Android 原生控件信息与应用适配规则；本项目没有训练自己的视觉识别模型。

### 4. 华为邮件适配与草稿回读

程序通过 `ACTION_SENDTO` / `mailto:` 打开原生邮件写信页并预填内容，再结合受限模型建议和无障碍字段输入修正草稿。已经正确的字段无需再次输入。回读必须匹配唯一目标收件人、完整中文主题、完整正文、网址及任务标记，并检查额外抄送、密送或附件；默认签名也在正文核对范围内。

每封任务邮件附带 `[SilverGuide:…]` 唯一标记，用于后续定位对应记录。准备完成后返回小银助手展示全部内容，用户可以编辑；编辑会重新准备并核对草稿，同时废除旧批准。

### 5. 发送授权、幂等与结果区分

- **批准绑定当前内容：** 请求携带 `task_id + draft_digest + approval_nonce`。过期摘要、旧批准或错误阶段不能触发发送；App 要求用户核对后勾选并确认。
- **发送前再次核对：** 恢复原来的写信页并读取内容；若草稿变化，进入 `needs_review`，要求重新处理。
- **先登记，再点击：** 在点击发送之前持久化 `send_attempts=1`；重复确认、同请求键重复提交、后端重启不能自动重放这次发送。
- **发送记录与收件分开：** 自动进入已发送邮件详情，核对收件人、主题、完整正文和任务标记，记录 `sent_record_verified`；收件人实际收到后的人工确认另记 `delivery_confirmed`。
- **不确定就只核验：** 已尝试发送却找不到足够证据时进入 `outcome_unknown`，提供 `verify_only`，不自动重发。

这些机制约束的是本系统的单次发送尝试，不能保证网络邮件系统端到端“恰好一次”送达。取消也不能撤回已经提交给邮件客户端的邮件。批准机制适用于当前受信任的本机客户端，尚未构建生产级多用户身份认证。

### 6. 实时进度、暂停与故障恢复

Android `TaskConnectionService` 以前台服务维持 WebSocket，在浏览器或邮件处于前台时继续显示通知、播报进度并接收暂停／继续／取消。客户端保存任务、事件游标和近期事件，断线后按游标回放；页面恢复时补读一次快照，原来的两秒任务进度轮询已移除。

暂停和取消在动作检查点生效；进行中的模型／系统调用受各自超时约束，不能保证瞬间中断。后端重启后，未完成且没有发送尝试的任务标为 `interrupted`，需要明确恢复；已登记发送尝试的未完成任务进入结果未知处理，不自动重放外部操作。

当前适配器还支持有界异常恢复：关闭已适配的非敏感原生提示后重新读取页面；等待短暂缺失的控件；写信页被切走时返回已记录的原草稿；字段输入未生效时重新定位并重试一次。每个浏览器／草稿准备阶段最多执行 3 次恢复尝试。未知或敏感弹窗、收件人／附件变化、重试耗尽都会停止，发送点击不进入恢复重试。恢复步骤通过原有 WebSocket 显示，并保存在 `recoveries.jsonl`。可复现的自动化与真机故障注入案例见 [异常恢复测试案例](docs/RECOVERY_TEST_CASES.md)。

常见状态包括 `needs_clarification`、`awaiting_qr`、`awaiting_confirmation`、`paused`、`needs_review`、`completed`、`failed`、`cancelled`、`interrupted` 和 `outcome_unknown`。`completed` 的含义要结合任务类型与证据标志判断，不能统一理解为“对方已收件”。

### 7. 中文语音与步骤解释

用户点击“说出您的需求”录制最多 20 秒的短音频，再点击“说完了，转成文字”。手机通过本地 API 将 AAC 录音交给电脑，由 faster-whisper / Whisper base 转写；识别完成后由用户核对文字。仅说“主题是……，正文是……”可填写手动表单，明确的扫码／分享目标才进入 Agent 流程。取消或离开录音页面会停止并清理临时录音，识别失败可改用文字输入。

中文播报使用手机已有 TTS 引擎。步骤解释由实际任务状态、失败原因和已有证据生成；`silverguide` 与 `basic` 模式共用规划、执行和安全规则，主要区别是反馈信息的详细程度。当前没有完成正式的适老用户效果评估。

### 8. 高德公交地铁路线与分步讲解

首页可展开“公交 / 地铁路线”表单，或从“演示案例 → 地图查询 → 正常”选用 T13／T14。自然语言理解仅提取明确的出发地、目的地、城市及偏好。先用“从 A 去 B”打通课堂查询；只给目的地时通过官方 `myLocation` URI 打开高德真实定位页面，读取完整地址与城市，并按手机当前用户核对定位权限、近期访问及实际定位时间／精度。仅在位置不超过 120 秒、精度不大于 100 米且非模拟定位时采纳；失败则返回小银补充起点。不以“我的位置”占位文字或演示地址冒充定位，也不新增小银定位权限。

独立高德适配器通过真实 GUI 搜索和选择地点、设置起点、切换公共交通、核对偏好并展开方案详情。UI 树提供当前文字、控件和边界；优先解析本次真实详情的原生分段结构，必要时本机 Apple Vision OCR 为零边界文字定位，受约束模型从当前详情文字／截图提取结构。最后按字段证据、起终点、选中方案和分段完整性核验，不把模型一句“完成”当作成功。未知费用等字段为 null；关键线路、方向、上下车站或步行连接未读全则显示 PARTIAL。步行总计只放总览，不重复分配给各段；详情中可核对的出入口与步行起终点进入卡片，未显示的街道转弯不会补猜。

结果包含总览、按顺序的大字步行／公交／地铁卡片、来源和查询时间。卡片与朗读共用同一份结构化事实；支持“第一步怎么走？”“我应该在哪一站下车？”“需要换乘几次？”。“换一个步行更少的方案”会真实重新查询，比较范围限于实际读取的候选和筛选。路线完成后保留结果，用户点击“准备新的任务”才清理，不套用邮件完成弹窗。

路线与邮件复用同一 worker、任务锁、暂停／继续／取消及 WebSocket；重查生成新 query_id，过期地点选择与迟到模型结果不能改变新查询。高德中文桥通过受 `DUMP` 权限保护的 Provider，仅接受当前高德包中，资源 ID、边界和原文字唯一匹配的可见输入框，原有邮件守卫不放宽。详细接口、预算、权限及版本范围见 [高德功能说明](docs/AMAP_TRANSIT.md)，可执行课堂案例见 [高德演示](docs/AMAP_DEMO.md)。

## 模型与程序的职责

| 环节 | 模型负责 | 程序负责 |
| --- | --- | --- |
| 需求理解 | 将自然语言提取为任务候选 | Schema 校验、联系人查找、地址来源核对、澄清与支持范围 |
| 界面感知 | 根据截图与候选判断已支持页面；高德使用独立感知和路线详情提取契约 | 获取观察、生成候选、校验目标和内容、实际执行 |
| 跨 App 推进 | 在受控节点提供理解／感知结果 | LangGraph 流程、Intent、设备操作、应用适配 |
| 发送授权 | 无批准权限 | 用户确认、摘要与 nonce 校验、发送前回读、单次尝试登记 |
| 完成判断 | 模型输出不直接作为成功证据 | 邮件核对已发送与人工收件；路线核对当前起终点、详情分段和字段来源 |

当前代码向 DeepSeek 请求的默认模型标识为 `deepseek-v4-flash-vision-exp`，文本理解与图像感知分开调用，可分别配置。该字符串是请求标识，实际服务端版本及其可用性由提供商决定。视觉调用包含图片，属于多模态使用；文字理解调用只提交对应的文字数据。具体实现见 [ModelGateway](backend/agent/models.py)。

这是一条带模型能力的受控工作流，不是让模型自由操纵任意 App 的通用代理。旧版“打开设置”等识别调试入口仍保留，但邮件操作必须走当前批准流程，旧的直接 Gmail 发信入口已禁用。

## 代码结构

```text
app/src/main/java/com/example/demo/
├── MainActivity.kt              # App 入口与主界面集成
├── WorkflowScreen.kt            # 任务输入、类型分支、预览、批准与进度
├── TransitUi.kt                 # 高德路线输入、地点选择、分段卡片与追问
├── TransitBridgeProvider.kt     # DUMP 保护的高德读写接口与有界单 worker
├── DemoCases.kt                 # 本地演示提示词，不预置路线结果
├── TaskConnectionService.kt     # WebSocket 前台服务、通知与播报
├── TaskCaptureActivity.kt       # 扫码生命周期与结果回传
├── LocalVoiceInput.kt           # 短录音、转写请求与清理
├── TaskInstruction.kt           # 语音／文字字段提取
├── MailAccessibilityService.kt  # 无障碍读取与字段输入
└── A11yCommandReceiver.kt       # 受限的 ADB 无障碍命令桥
backend/
├── main.py                     # 统一启动入口，加载本地 .env
├── api/                        # API、Schema、WebSocket 事件
├── agent/                      # LangGraph、运行时、模型网关
├── perception/                 # 界面观察和字段候选
├── safety/                     # 模型动作守卫
├── device/                     # Mobilerun、华为邮件与高德 GUI 适配
├── transit/                    # 路线证据验证、分段去重、解释与即时追问
├── verification/               # 邮件内容与已发送证据核验
├── storage/                    # SQLite 任务、联系人与事件
├── explanation/                # 基于事实的步骤解释
├── evaluation/                 # 试运行指标与证据统计
├── domain.py                   # URL、草稿等公共规则
├── speech_api.py               # 本地中文语音接口
├── test_*.py                   # 后端回归测试
└── gmail_demo.py 等             # 历史兼容与底层辅助；非当前 Gmail 发信流程
tools/                          # 语音模型准备、评估、证据清理与录屏辅助
docs/                           # 架构、接口、验收、演示和课程报告
.github/workflows/ci.yml         # 后端与 Android CI
```

## 安装与运行

### 前置条件

- 一台电脑，Python 3.12、JDK 25、Android SDK Platform Tools 与 API 37 SDK。Mac 已做本机运行验证；Windows 可使用相同 Python 代码和 Gradle Wrapper，完整的 Windows 真机链路尚未单独验收。
- 一台支持 USB 调试的 Android 手机。最低 SDK 为 24，但当前应用适配实测范围是华为 ELS-AN00 / Android 12，不能据此推定所有 Android 7+ 设备均兼容。
- 手机上已登录华为“电子邮件”，并准备一个自己有权使用的测试收件邮箱；自动化过程中保持手机解锁。
- 可用的模型 API 密钥和网络。设置好 `JAVA_HOME`、`ANDROID_HOME`，将 SDK 的 `platform-tools` 加入 `PATH`。SDK Manager 对应平台包为 `platforms;android-37.0`。

```bash
git clone https://github.com/yzcchally/project_demo.git
cd project_demo
```

Android Studio 可打开仓库根目录。若本机没有 `local.properties`，可由 Android Studio 写入本机 SDK 路径，或正确设置 `ANDROID_HOME`；不要把本机路径提交到 Git。

### 1. 创建 Python 环境

macOS / Linux 终端：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
test -f .env || cp .env.example .env
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

仅运行后端时可安装 `backend/requirements.txt`；开发依赖另外包含测试工具。若沿用本机用 uv 创建且没有 pip 的虚拟环境，可使用 `uv pip install --python .venv/bin/python -r backend/requirements-dev.txt`。

编辑本地 `.env`，填写自己的密钥，不要提交此文件：

```dotenv
DEEPSEEK_API_KEY=请填入自己的密钥
SILVERGUIDE_TEXT_MODEL=deepseek-v4-flash-vision-exp
SILVERGUIDE_VISION_MODEL=deepseek-v4-flash-vision-exp
SILVERGUIDE_MODEL_BASE_URL=https://api.deepseek.com
SILVERGUIDE_DEVICE_BACKEND=mobilerun
```

文本和视觉模型可分别替换，但视觉模型必须支持当前图片请求格式。更换提供商时需检查 API、模型标识和密钥读取方式；当前读取的密钥变量仍为 `DEEPSEEK_API_KEY`。`ADB_PATH` 可指定 ADB 路径，`SILVERGUIDE_DEVICE_BACKEND=adb` 可选择原生适配回退；这不等于更换了应用适配范围。

### 2. 可选：准备本地语音

macOS：

```bash
.venv/bin/python -m pip install -r backend/requirements-speech.txt
.venv/bin/python tools/prepare_speech.py
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-speech.txt
.\.venv\Scripts\python.exe tools/prepare_speech.py
```

模型准备脚本首次需要联网下载，将固定 revision 的 Whisper base 转换模型保存到 `.run/models/whisper-base/`。启动后端后可通过 `/speech/status` 检查就绪情况。自定义目录使用 `SPEECH_MODEL_PATH`。未安装语音依赖或模型时仍可使用文字输入。`av==16.1.0` 是当前解码器验证过的配套版本，不建议单独升级而不验证。

### 3. 启动后端

macOS：

```bash
./start_backend.command
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe backend/main.py
```

保持此终端运行。检查 [健康接口](http://127.0.0.1:8000/health) 和 [交互式 API 文档](http://127.0.0.1:8000/docs)。macOS 也可直接执行 `.venv/bin/python backend/main.py`。

### 4. 构建、连接与安装手机 App

手机开启开发者选项和 USB 调试，用支持数据传输的 USB 线连接电脑，在手机上允许调试授权。`adb devices -l` 应显示一台状态为 `device` 的手机。

macOS 的仓库脚本会构建、设置端口转发、安装并启动 App：

```bash
./build_android.command assembleDebug
./connect_phone.command
```

Windows PowerShell 在另一个终端执行：

```powershell
.\gradlew.bat assembleDebug
adb devices -l
adb reverse tcp:8000 tcp:8000
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.example.demo/.MainActivity
```

安装后，**系统中的应用名和无障碍服务名仍是 `Demo`，App 页面显示“小银助手”**。请手动开启 Demo 无障碍服务，并根据需要允许相机、麦克风和通知权限。在 App 中检查手机连接，保持解锁后开始测试。手机重新连接或重启后，应重新执行 `adb reverse tcp:8000 tcp:8000`。

## 完整演示案例

App 首页右上角的 **“演示案例”** 先显示两个类型按钮：**“地图查询”** 和 **“邮件案例”**。两类都可按“推荐／正常／异常／恢复”筛选；切换类型后回到该类型的“推荐”。共有 19 个可见案例（T02–T20）；T01 仅保留后端兼容和备用技术检查。

| 类型入口 | 案例与筛选 |
| --- | --- |
| 地图查询 → 推荐 | T13、T15、T18：完整查询、起点澄清、补充起点后继续。 |
| 地图查询 → 正常 | T13“高德公交地铁路线”、T14“当前位置完整查询”。 |
| 地图查询 → 异常 | T15“出发地不明确”、T16“目的地不明确”、T17“取消路线查询”。 |
| 地图查询 → 恢复 | T18“补充起点后继续”、T19“暂停后继续查询”、T20“恢复网络后重查”。 |
| 邮件案例 | T02–T12；推荐为 T02、T04、T12。该页同时收纳扫码、网页校验、任务控制与不支持请求拦截等辅助测试。 |

**“邮件案例 → 正常”只有 T02“完整扫码发信演示”**，以一个完整任务展示扫码、网页核对、邮件草稿、确认发送及结果核验。地图正常案例同样一次启动完整任务，不显示分步说明；异常与恢复案例保留可展开的 3–5 步操作。选择后自动填入提示词，邮件另生成新主题编号，点击开始才执行。T12 可从普通后端显式启用一次中断恢复演示；T15–T20 不启用故障注入，仅用已有澄清、取消、暂停／继续和重查能力。操作、二维码和记录表见 [手动测试案例](docs/MANUAL_TEST_CASES.md)，自动化恢复检查另见 [恢复测试案例](docs/RECOVERY_TEST_CASES.md)。

课堂地图演示可从“地图查询 → 推荐”依次选择 **T13 → T15 → T18**；需要定位时选 T14。T19 在页面无法安全恢复时应提示重新查询，不保证任意页面都能原地继续。T20 需本人手动关闭手机网络、保留电脑联网和 USB 连接，再恢复网络重查；高德若使用缓存返回结果，应记录“故障未复现”。高德演示也可直接在首页路线表单填写公共起终点，无需邮箱。各案例操作见 [AMAP_DEMO.md](docs/AMAP_DEMO.md)，是否真机通过以 [AMAP_ACCEPTANCE.md](docs/AMAP_ACCEPTANCE.md) 为准，内置案例不等于已完成该轮实测。

以下是原有扫码邮件完整演示。先用自己的两个测试账号，确认发件账号已在华为邮件登录，收件账号能够由本人查看。

在“演示案例 → 邮件案例 → 正常”选择“完整扫码发信演示”，点击“使用完整案例”，再点击 **“开始完整演示”**。扫描下方二维码后，系统连续核对 `https://example.org/`、准备邮件并返回草稿预览；本人核对完整收件人、主题、正文、网址和任务标记，勾选核对选项并确认发送，随后等待已发送核验，在收件箱确实找到内容正确的邮件后回 App 确认收件。确认成功后显示 **“任务已完成，请点击确认”**，点击 **“确认，返回首页”**，本轮输入、选中案例、进度和表单清空，页面回到顶部；已保存联系人、语音偏好和后端历史保留。若联系人称呼尚未保存，先填写本人能查看的测试邮箱。整条任务只需启动一次，发送前仍须本人确认；不要因等待而自行重复点击原生邮件发送键。

邮件任务须在已完成且人工收件确认成功后才显示完成弹窗；仅核对到已发送记录、尚未确认收件时不会显示。仅打开网页的任务则须已完成且网址核对通过。失败、取消或结果未知不触发此完成提示。

<a href="docs/fixtures/share-link-qr.png"><img src="docs/fixtures/share-link-qr.png" alt="SilverGuide 测试二维码：https://example.org/" width="280"></a>

[打开完整二维码图片](docs/fixtures/share-link-qr.png)。发送前暂停／继续／取消可作为补充演示；取消测试不要再确认发送。已有的本机录屏与试运行记录未随源码公开，演示脚本、场景及证据口径见 [演示与评估文档](docs/DEMO_AND_EVALUATION.md)。

## 后端接口

主任务入口使用 `/api` 路径；完整字段约定见 [API_CONTRACT.md](docs/API_CONTRACT.md)，运行时以 `/docs` 为准。

| 接口 | 用途 |
| --- | --- |
| `POST /api/tasks` | 创建自然语言任务，携带 `instruction`、`request_key` 和可选评估模式信息 |
| `GET /api/tasks/latest`、`GET /api/tasks/{id}` | 获取最近任务或指定任务的完整状态与证据标志 |
| `POST /api/tasks/{id}/clarify`、`/qr` | 提交明确澄清信息或当前轮次的真实扫码结果 |
| `PATCH /api/tasks/{id}/draft` | 编辑草稿，废除旧批准并重新准备 |
| `POST /api/tasks/{id}/confirm` | 提交与当前摘要、批准 nonce 绑定的明确确认 |
| `POST /api/tasks/{id}/pause`、`/resume`、`/cancel` | 控制当前任务 |
| `POST /api/tasks/{id}/control` | 包含发送前重试 `retry_prepare`、发送后仅核验 `verify_only` |
| `POST /api/tasks/{id}/received` | 记录用户核对实际收件的确认 |
| `POST /api/tasks/{id}/transit/clarify`、`/transit/place` | 提交当前 query_id 的路线澄清或带 nonce 的地点选择 |
| `POST /api/tasks/{id}/transit/requery`、`/transit/show` | 重新查询或返回高德；共享设备互斥，重查使用新 query_id |
| `POST /api/tasks/{id}/transit/ask` | 根据当前已验证路线回答即时追问 |
| `GET /api/tasks/{id}/events?after=0`、`/explain` | 有序事件及当前状态解释；事件每页最多 100 条 |
| `GET/PUT /api/contacts`、`DELETE /api/contacts/{alias}` | 管理明确保存的称呼与邮箱 |
| `GET /api/system/graph`、`GET /api/evaluation` | 实际编译图的 Mermaid、试运行评估 |
| `GET /speech/status`、`POST /speech/transcribe` | 本地语音就绪检查和短音频转写 |
| `WS /ws/tasks/{id}?after=<cursor>` | 首次快照、实时进度、心跳与断线游标恢复 |

## 测试与验收

本轮高德扩展、T14 定位和校区地点选择修复后，后端回归 **642 项＋13 个子测试通过**，Android **32 项 JVM 测试、Debug 构建及 lint 通过**。已完成两组不同起终点的真实完整查询（约 76 秒、78 秒），以及一组换乘重查（约 74 秒）、博物馆显式起点（约 84 秒）、真实定位出发的五步行程（约 89 秒）和华南理工大学广州国际校区出发的五步行程（约 99 秒）、部分读取、查看原高德详情、中文朗读和取消验证。具体版本、证据、换乘补充结果及待验范围见 [高德验收记录](docs/AMAP_ACCEPTANCE.md)。

macOS 本地检查：

```bash
.venv/bin/python -m pytest backend -q
./build_android.command assembleDebug testDebugUnitTest lintDebug
.venv/bin/python tools/evaluate.py
```

Windows 对应使用 `.\.venv\Scripts\python.exe` 和 `.\gradlew.bat`。测试中的设备／模型替身用于覆盖故障边界，不需要真实密钥，也不能代替真机验收。

以下为新增高德功能前的既有邮件与案例基线；本轮高德相关构建、测试和真机结论单独见 [AMAP_ACCEPTANCE.md](docs/AMAP_ACCEPTANCE.md)：

- 后端 **177 项测试通过，另有 13 个 unittest 子测试通过**。原有检查覆盖图中断、联系人、扫码 nonce、动作守卫、批准、幂等、发送故障和 WebSocket；新增 71 项恢复检查及 36 项案例元数据和单次演示故障注入检查。
- Android **14 项 JVM 测试通过**，Debug 构建与 lint 完成；新增 10 项案例完整性、主题编号和演示请求限制检查。远端 CI 结果以文首徽章对应的已发布提交为准。
- 真机主链路完成真实扫码、完整网页地址核对、中文草稿、用户批准、一次发送、已发送详情核验，以及用户确认实际收件；中文语音和后台通知控制也有单独证据。
- 2026-11-11 的独立恢复案例使用明确网址，在真实写信页注入一次 HOME；系统自动返回原草稿，完整回读通过并停在等待用户批准，随后取消，发送始终为 0。真实原生提示窗口及恢复后重新扫码发信仍需各自验收，详见 [恢复测试案例](docs/RECOVERY_TEST_CASES.md)。
- 5 个预先定义的真实模型理解样本通过，**这是需求理解小样本，不是视觉识别准确率或 GUI 任务成功率**。

正式的 **12 场景 × 3 次、共 36 次真机实验**、B0/B1 对照和老年用户研究尚未完成。录屏中的一次模型建议被安全守卫拒绝后，由开发者修复适配再重试，应算开发者参与的过程，不能当作自主恢复。不能据现有工程测试声称已经证明老年人的独立完成率、信任或认知负荷改善。

`tools/evaluate.py` 从持久化任务生成试运行报告：成功需要证据，安全停止、失败和结果未知分别统计；未测量指标保留 `null`。当前任务时长包含人工等待及后续核查，不代表纯模型或 GUI 执行耗时。详细定义见 [验收记录](docs/ACCEPTANCE.md) 和 [评估协议](docs/DEMO_AND_EVALUATION.md)。

## 数据与隐私

| 数据 | 当前存放与使用方式 |
| --- | --- |
| 模型密钥 | 电脑本地 `.env`，由后端读取，不打包进 APK；该文件被 Git 忽略 |
| 任务、联系人、发送尝试 | `.run/workflows/` 下的 SQLite 数据库 |
| 图执行检查点 | `.run/workflows/checkpoints.sqlite3` |
| 界面与执行证据 | 任务目录内的事件、动作、UI XML、路线截图及分段证据等；可能包含真实邮箱、邮件内容和个人位置 |
| 模型调用元数据 | 阶段、请求模型名、输入摘要、token、耗时及结果；不记录原始提示词或密钥 |
| 语音 | 手机临时录音经 USB 转发交给电脑本地 ASR，完成或取消后清理临时音频；模型文件保留本机 |

**本地语音识别不代表所有数据都留在本机。** 自然语言需求、用于感知的截图、界面候选、预期草稿及高德路线详情会发送给所配置的云端模型服务；演示时应使用获得授权的测试账号与内容。

`.env`、虚拟环境、本机 SDK 路径、构建产物及 `.run/` 中的数据库、日志、截图和录屏不随代码提交。证据清理工具 `tools/prune_evidence.py` 默认仅预览 30 天前终态任务的屏幕文件，显式 `--apply` 才删除，保留任务及防重复记录；这不是完整的数据匿名化或自动保留期限系统。

当前后端只监听回环地址，并限制 Host 与 WebSocket 网页 Origin；没有生产级远程账号认证、多租户隔离或多设备调度，不应直接改成公网服务使用。

## 常见问题与后续工作

| 现象 | 检查与处理 |
| --- | --- |
| `adb devices` 显示 `unauthorized` 或无设备 | 解锁手机，允许 USB 调试，检查数据线和连接模式；本项目按单手机使用 |
| App 连不上后端 | 保持后端终端运行，检查 `/health`，重新执行 `adb reverse tcp:8000 tcp:8000` |
| 可以打开 App，但不能读写邮件字段 | 在系统设置中启用 **Demo 无障碍服务**，保持解锁；重新安装后需检查服务是否仍开启 |
| 语音没有转出文字 | 允许麦克风权限，点击“说完了，转成文字”，检查 `/speech/status` 与语音模型；失败时可用文字输入 |
| 浏览器地址不一致或模型动作被拒绝 | 查看当前解释和证据，确认应用及页面属于适配范围；不要绕过守卫或把页面正文当作地址 |
| 高德返回部分路线 | 查看缺失字段和本次高德详情；不要把列表摘要或未知方向补成完整行程 |
| 高德无法核实当前位置 | 在小银填写明确起点和城市；不能用课堂示例冒充定位 |
| 高德路线按钮不可定位 | 检查高德版本、页面和 Mac 命令行工具；OCR 回退失败时停止，不改成固定坐标盲点 |
| 发信后结果未知 | 只重新核验已发送和收件情况，不自动重发；确认取消不能撤回已提交邮件 |

后续优先方向包括：更多手机／应用版本的兼容性、Outlook 等邮件适配、失败重试后的前台服务与进度展示边界、系统性异常真机测试、正式对照实验及适老用户研究。当前没有实现手机脱离电脑独立运行，也没有任意 App、支付或密码操作能力。

扫码模块沿用已确认的小银 App 内置方案；高德为新增的外部应用流程。各功能的真实验收范围分别保留记录，不能从某条邮件成功推算所有路线、地图版本或其他邮件客户端均已适配。

## 更多文档

| 文档 | 内容 |
| --- | --- |
| [简明运行说明](docs/README.md) | 已配置环境的日常启动与开发命令 |
| [Mac 环境说明](docs/MAC_SETUP.md) | 本机环境配置与早期运行过程 |
| [架构设计](docs/ARCHITECTURE.md) | 模块边界、执行与持久化设计 |
| [接口契约](docs/API_CONTRACT.md) | 请求、事件、状态及幂等约定 |
| [验收记录](docs/ACCEPTANCE.md) | 当前真实证据、限制与尚未完成的验收 |
| [高德功能说明](docs/AMAP_TRANSIT.md) | 路线架构、GUI 读取、权限、接口与支持范围 |
| [高德课堂演示](docs/AMAP_DEMO.md) | 两组可配置公共地点、正常演示与异常处理 |
| [高德验收记录](docs/AMAP_ACCEPTANCE.md) | 本轮实际命令、测试、真机证据、耗时及待验项 |
| [演示与评估](docs/DEMO_AND_EVALUATION.md) | 演示步骤、12 场景及评估口径 |
| [英文实现报告](docs/IMPLEMENTATION_REPORT_EN.md) | 课程报告源文档 |
| [AI 使用与团队贡献](docs/AI_USAGE_AND_CONTRIBUTIONS.md) | AI 辅助说明与待团队填写的真实成员贡献 |
