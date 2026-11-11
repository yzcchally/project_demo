# 异常恢复测试案例

本文件对应当前华为浏览器／Chrome 原生提示和华为“电子邮件”适配范围。目标是验证：异常出现后重新观察，执行允许的有限恢复，再核对真实页面继续任务；重要外部操作仍由用户决定。

需要直接复制到 App 的成功／失败提示词时，先看 [手动测试案例](MANUAL_TEST_CASES.md)。本文侧重开发回归、故障注入和证据判定。

新版可在“演示案例 → 恢复”直接选择 T12 后点击开始；普通后端根据本任务显式携带的案例和故障参数注入一次。下文 CLI 保留为独立开发复现方式，不再是手机演示的必需步骤。

## 恢复范围与成功标准

- 已知纯提示窗口：核对原生 Dialog、所属 App、标题、按钮和可见范围后，仅关闭明确允许的提示。执行前再读一次界面；窗口已消失就不点击旧位置。
- 邮件控件暂时未加载：有限等待并重新读取，不新建第二封邮件。
- 原写信页被切到后台：只恢复已经记录的原 Activity，重新读取原草稿，不通过新的 `mailto:` 创建替代草稿。
- 字段输入未生效：重新定位字段，最多再尝试一次；只有实际回读匹配才报告输入恢复。
- 未知或涉及发送、删除、丢弃草稿、账号、权限、密码、支付等窗口：停止自动处理，保留用户决定入口。
- 发送按钮：没有自动重试。发送尝试先持久化，结果不确定时只能重新核验。

打开网页与准备草稿各有最多 3 次恢复尝试；写信页观察默认最多 4 轮。点击提示前的额外复查也会产生一次界面读取，所以观察次数不等于恢复动作次数。等待不增加外部发送次数。预算耗尽应停止，不循环点击。

`recovered` 表示某次页面或输入恢复通过了对应检查，**不等于整项任务完成或邮件已经送达**。邮件仍需完整草稿回读、当前版本批准、单次发送及独立结果核验。

## 自动化检查

在项目根目录执行：

```bash
.venv/bin/python -m pytest backend/test_recovery.py -q
```

完整后端回归：

```bash
.venv/bin/python -m pytest backend -q
```

Windows 将 `.venv/bin/python` 替换为 `.\.venv\Scripts\python.exe`。测试使用内存界面树、设备／模型替身和临时数据库，**不连接手机、不调用云端模型、不发送真实邮件**。

2026-11-11 开发检查：新增 `backend/test_recovery.py` 的 **71 项检查通过**；整个后端 **141 项测试和 13 个子测试通过**，本次结果保存在 `.run/recovery-tests.xml`。参数化检查包含多个按钮文案和支持包的组合；此数量不是 71 个真机试验。该结果来自本地运行，远端 CI 需单独记录。

| 案例 | 故障与条件 | 预期行为 | 对应自动化检查 |
| --- | --- | --- | --- |
| REC-01 原生纯提示 | 支持 App 出现已知提示，取消／稍后按钮唯一、可见、可用 | 执行前复查，关闭后再读取写信页或浏览器完整原生地址 | `test_known_informational_dialog_can_choose_only_its_native_negative_button`、`test_hint_is_dismissed_before_a_fresh_composer_is_accepted`、`test_browser_hint_is_dismissed_and_the_native_full_address_is_read_again` |
| REC-02 窗口变化 | 第一次看到可关闭提示，第二次提示已消失或变成敏感窗口；最终点击检查点发生暂停恢复 | 消失则跳过旧点击；变敏感立即停止；上下文变化后丢弃旧界面再读，不能点击旧坐标 | `test_hint_that_disappears_before_the_click_is_not_clicked_at_its_old_position`、`test_safe_hint_replaced_by_sensitive_dialog_between_observation_and_click_is_blocked`、`test_pause_resume_at_final_tap_checkpoint_skips_the_old_hint_and_reobserves`、`test_observe_discards_xml_read_across_a_resume_and_saves_only_the_fresh_screen` |
| REC-03 控件延迟 | 第一轮写信页暂缺正文控件；或浏览器冷加载进度条仍存在 | 等待并重新读取；浏览器加载中不聚焦地址栏，加载后才读取完整原生地址，不推断缩短域名 | `test_transient_missing_editor_waits_for_current_screen_without_opening_a_new_draft`、`test_browser_cold_load_waits_for_native_progress_before_focusing_the_address_bar` |
| REC-04 原草稿被切走 | 已记录原写信 Activity，随后观察到桌面 | 返回原 Activity，再读当前草稿；不新建邮件、不丢弃原草稿 | `test_leaving_composer_restores_only_the_recorded_activity_then_reobserves` |
| REC-05 控件一直缺失 | 正文控件在观察上限内始终不存在 | 原 Activity 最多恢复一次，之后明确停止 | `test_permanently_missing_editor_restores_original_composer_at_most_once_then_stops` |
| REC-06 输入未生效 | 首次字段写入后读到旧值 | 重新定位并使用备用输入方式；再次失败则停止，不能虚报恢复 | `test_input_retry_is_freshly_located_and_reported_recovered_only_after_matching_readback` |
| REC-07 敏感／未知窗口 | 发送、删除、丢弃、权限、登录等窗口；或不认识的弹窗 | 不自动确认，也不以“取消所有窗口”作为恢复策略 | `test_sensitive_dialog_is_blocked_even_if_it_has_a_cancel_button`、`test_unknown_modal_requires_human_intervention` |
| REC-08 无法安全定位 | 控件重复、不可见、不可用、包归属不符，或网页内容伪装成系统按钮 | 不点击；网页中的假提示不能授权操作 | `test_dialog_button_must_be_visible_enabled_and_owned_by_the_expected_app`、`test_duplicate_native_button_is_not_a_unique_click_target`、`test_page_content_with_native_looking_ids_cannot_authorize_a_dialog_click` |
| REC-09 恢复次数耗尽／用户取消 | 提示反复出现，或检查点收到取消 | 有限停止；取消后不执行后续恢复动作 | `test_repeated_hint_exhausts_a_bounded_action_budget_instead_of_looping`、`test_cancel_checkpoint_interrupts_recovery_before_an_external_action` |
| REC-10 草稿内容发生变化 | 恢复页面后出现其他收件人、其他任务标记，或原批准后的草稿变化 | 不覆盖其他草稿、不沿用旧批准发送；要求重新核对 | `test_recipient_change_after_page_recovery_is_not_automatically_overwritten`、`test_restored_composer_with_same_recipient_and_subject_but_another_task_marker_is_rejected`；已有 `test_changed_phone_draft_invalidates_approval` |
| REC-11 恢复后等待批准 | 写信页提示已处理，草稿全部核对 | 停在 `awaiting_confirmation`，`send_attempts=0` | `test_recovery_keeps_the_normal_user_approval_gate` |
| REC-12 发送结果不明 | 点击发送时设备调用报错，无法确定外部效果 | 不再次点击发送；工作流保留单次尝试，允许仅核验 | `test_failed_send_click_is_not_replayed_by_recovery`；已有 `test_disconnect_after_dispatch_is_unknown_and_not_retryable` |

## 真机案例 A：写信页被切到桌面后自动恢复

这是主要的可复现实机案例，对应“失败动作／页面中断后的恢复”。测试驱动器只在第一次实际观察到完整写信页后按一次 HOME，随后交给正式恢复代码处理。它不生成假的界面树、不代替恢复、不编辑或发送邮件。

### 前置条件

1. 华为手机已解锁并允许 USB 调试，Demo 无障碍服务开启，华为邮件已登录测试账号。
2. 没有正在执行的旧任务。旧任务存在时，先在 App 中完成或取消。
3. 常规后端终端按 `Ctrl+C` 停止，确保电脑的 8000 端口空闲。演示后端与常规后端不能并行操作同一手机及任务库。
4. 使用本人控制的测试收件邮箱。第一轮可只验证到草稿，不必真的发送。

### 步骤

在 Mac 的项目根目录启动故障注入后端：

```bash
.venv/bin/python tools/run_recovery_demo.py --case composer-background
```

另一个终端恢复 USB 转发：

```bash
"$HOME/Library/Android/sdk/platform-tools/adb" reverse tcp:8000 tcp:8000
```

手机打开小银助手，点击“准备新的任务”，输入：

> 帮我把 https://example.org/ 发给测试收件人，主题是恢复测试加本轮时间，正文是这是写信页中断后的恢复测试。

“测试收件人”应为自己保存的明确邮箱称呼；若未保存，按 App 提示提供本人控制的完整地址。

1. 系统打开并核对网页，进入华为邮件写信页。
2. 驱动器实际读到当前收件人和原写信 Activity 后，主动按一次 HOME；手机短暂显示桌面。
3. 正式恢复逻辑等待、返回已记录的原写信 Activity，再读取收件人、主题和完整正文，继续准备。
4. App 返回草稿预览。**第一轮停在这里核对，再取消本轮**，以零发送验证恢复能力。
5. 如需额外验证发信，由用户本人核对草稿并批准；系统仍只尝试一次，随后核验已发送记录。不能把停止在草稿处报告成“已完成发信”。

### 通过标准

- 保留同一个 `task_id`，没有靠修改代码、重新创建任务或代操作输入完成恢复。
- 有 `recovery-fault-injection.json`，说明实际故障是 `home_after_observed_composer`。
- `recoveries.jsonl` 中有 `restore_composer` 的恢复尝试和恢复后观察通过的记录。
- 只创建过一次原邮件写信页，恢复使用原 Activity；收件人、主题、完整正文及网址均符合本轮任务。
- 第一轮达到 `awaiting_confirmation`、`draft_verified=true`、`send_attempts=0`；取消后为 `cancelled`，发送仍为 0。
- 若用户另行批准真实发信，应另外记录 `send_attempts=1`、独立已发送核验结果；收件确认另记。

每次重新启动演示后端只注入一次故障。需要下一次同样试验时，先结束或取消当前任务，停止演示后端，再启动上述命令。仅在同一进程创建第二个任务不会再次注入。

**证据边界：** 这个案例证明真实手机的页面中断恢复，不等于真机已测试所有原生弹窗。自然语言明确分享网址也不等于本轮重新做了真实扫码；如需验证扫码全链路，请另做案例 C。

## 真机案例 B：已知原生提示窗口

本案例需要在当前适配应用中真实出现受支持的纯信息提示。先记录应用版本、标题、按钮及触发方式；如果本机没有可重复触发的对应窗口，该项保持“未执行”，不要用 XML 替身结果填写真机通过。

1. 使用本轮独立主题，发起打开网页或准备邮件任务。
2. 在准备阶段触发已知的原生“使用提示／邮件填写提示／更新提示”等纯提示窗口。
3. 观察系统是否只选择允许的取消／稍后／关闭按钮，处理后重新读取页面并继续核对。
4. 在预览阶段核对草稿并取消，本轮发送应为 0。

通过标准：记录真实窗口截图和所属包；有动作前重新观察证据、允许的关闭目标及恢复后页面证据。敏感、未知或不具备安全关闭按钮的窗口应停止，属于预期的安全处理，不能记为自动恢复成功。

## 真机案例 C：恢复后继续完整扫码分享

此项用于把异常恢复与原有主流程组合；应单独记录，不能拿此前成功发信替代本轮测试。

1. 使用演示后端，按 App 自然语言入口发起“扫描二维码并通过邮件分享”的任务。
2. 扫描 [实际测试二维码](fixtures/share-link-qr.png)，其内容为 `https://example.org/`。
3. 观察网页核对、真实 HOME 故障注入、返回原写信页以及完整草稿回读。
4. 用户本人批准后只发送一次，系统核验匹配的已发送详情；收件人实际收到并核对后再确认收件。

通过标准：同一任务有真实 QR 回调、网页核对、恢复日志、草稿证据、用户批准、一次发送和外部结果证据。实际收件确认与已发送证据分开记录。

## 记录结果与复核

每轮记录以下信息；“预期”不能替代“实际”：

| 字段 | 填写内容 |
| --- | --- |
| 测试 ID／任务 ID | REC 编号、独立试验编号、真实 `task_id` |
| 环境 | 手机／Android、浏览器／邮件版本、当前提交和模型请求标识 |
| 起始条件 | 手动提供网址或真实扫码、测试别名来源、是否已有草稿 |
| 注入故障 | 实际发生方式、一次性驱动或人工触发、发生阶段 |
| 恢复过程 | 实际观察、动作、次数、恢复后检查；中途是否有人代操作或改代码 |
| 最终状态 | `awaiting_confirmation`／`cancelled`／`completed`／`outcome_unknown` 等 |
| 发送与证据 | `send_attempts`、`draft_verified`、`sent_record_verified`、`delivery_confirmed` 分别记录 |
| 结论 | 通过／预期安全停止／失败／未执行；附证据路径 |

本地任务目录为 `.run/workflows/<task_id>/`：

- `recoveries.jsonl`：`time`、`stage`、`action`、`outcome`、`attempt` 和中文消息；不写入邮件正文。
- `events.jsonl`：同一任务的进度及最终状态；App 可实时显示恢复提示。
- `actions.jsonl` 与 `screen-*.xml`：真实设备动作和关键界面读取证据，可能包含私人测试内容。
- `recovery-fault-injection.json`：只在明确开启的 HOME 故障注入演示中产生。

任务 API `GET /api/tasks/{id}` 还提供 `recovery_count`、`last_recovery` 和近期 `recovery_events`。没有事件的旧任务可以没有这些字段。任务和证据保存在本机，不随 Git 公开；分享给教师前检查测试账号及内容。

## 执行状态

| 层次 | 当前可声明的结果 |
| --- | --- |
| 自动化恢复逻辑 | 新增 71 项检查通过；含提示处理、消失／变化窗口、暂停恢复后的旧坐标阻断、缺字段、浏览器冷加载等待、原草稿身份检查、输入重试、保护批准和发送不重放 |
| 真实手机 HOME 中断 | 2026-11-11 案例 A 通过：任务 `b6958b7941b24b639394e639e7573675` 只创建一次写信页，真实切到桌面一次，自动恢复原草稿并回读通过；手机与后端均到 `awaiting_confirmation`，随后取消，发送始终为 0 |
| 真实原生提示窗口 | 需要按案例 B 记录本机触发条件和证据，不由合成 Dialog 测试推定 |
| 恢复后完整扫码发信 | 需要按案例 C 独立执行并记录；仅恢复到草稿不算发信完成 |

案例 A 的本地证据在 `.run/recovery-live-20261111/result.json`、
`recovered-draft-preview.png` 以及对应任务的 `recoveries.jsonl`、
`recovery-fault-injection.json`、`actions.jsonl`、`screen-*.xml`。
实际观察包含华为桌面；原草稿恢复后手机缓存和后端状态一致。测试结束后已切回普通后端，故障注入未继续启用。

另保留一次前置失败：任务 `823ba437cd1c4847afd9f95b0b7e3206` 在浏览器首次加载时未取得完整地址，安全停止、发送 0 次，尚未触发 HOME。增加“原生加载进度条消失前不聚焦地址栏”的检查和回归后，重新启动测试进程，再独立执行上述成功案例。前置失败记录为 `.run/recovery-live-20261111/initial-browser-failure.json`；它不计为恢复成功。
