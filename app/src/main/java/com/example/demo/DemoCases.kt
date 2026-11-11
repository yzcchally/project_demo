package com.example.demo

enum class DemoCategory(val label: String) {
    SUCCESS("正常流程"),
    CHECKS("异常与控制"),
    RECOVERY("异常恢复"),
}

data class DemoCase(
    val id: String,
    val title: String,
    val summary: String,
    val category: DemoCategory,
    val promptTemplate: String,
    val steps: List<String>,
    val expected: String,
    val requiresQr: Boolean = false,
    val recoveryFault: String? = null,
    val recommended: Boolean = false,
)

/** Local examples only. Choosing one prepares text; it never starts a task. */
object DemoCases {
    val all: List<DemoCase> = listOf(
        DemoCase(
            id = "T02",
            title = "完整扫码发信演示",
            summary = "开始后扫描现场的 HTTPS 二维码，小银会打开网页、核对网址并准备邮件；您在 App 核对收件人、主题、正文和网址后确认发送，最后查看收件邮箱并回 App 确认收到，再确认完成、返回首页。",
            category = DemoCategory.SUCCESS,
            promptTemplate = "帮我扫描二维码，把网址发给测试收件人，主题是 SilverGuide 扫码成功 T02-{run}，正文是这是完整扫码发信测试，请查看这个网页。",
            steps = emptyList(),
            expected = "确认前等待您的决定；确认后只尝试发送一次，并核对已发送记录。",
            requiresQr = true,
            recommended = true,
        ),
        DemoCase(
            id = "T03",
            title = "草稿变更后重新核对",
            summary = "修改内容后重新核对，再决定是否发送。",
            category = DemoCategory.CHECKS,
            promptTemplate = "帮我把 https://example.org/ 发给测试收件人，主题是 SilverGuide 草稿修改 T03-{run}，正文是修改前的测试正文。",
            steps = listOf(
                "启动任务，等待完整草稿预览。",
                "点击“修改草稿内容”，修改主题或正文。",
                "保存修改并重新核对，查看新的预览内容。",
                "第一轮观察后取消本轮任务即可。",
            ),
            expected = "新内容重新回读，并再次等待您核对确认；观察后取消则不发送。",
        ),
        DemoCase(
            id = "T04",
            title = "缺少收件信息",
            summary = "联系人未知时，先询问完整邮箱。",
            category = DemoCategory.CHECKS,
            promptTemplate = "帮我把 https://example.org/ 发给演示联系人甲，主题是 SilverGuide 缺少联系人 T04-{run}，正文是请查看这个网页。",
            steps = listOf(
                "确认联系人称呼中没有保存“演示联系人甲”。",
                "启动任务，观察是否询问完整收件邮箱。",
                "第一轮不保存这个称呼，观察后取消任务。",
            ),
            expected = "停下来询问完整邮箱，不猜测联系人地址，也不发送邮件。",
            recommended = true,
        ),
        DemoCase(
            id = "T05",
            title = "收件人有歧义",
            summary = "给出两个邮箱，检查是否让您明确选择。",
            category = DemoCategory.CHECKS,
            promptTemplate = "帮我把 https://example.org/ 发给 person-a@example.org 或 person-b@example.org，主题是 SilverGuide 收件人冲突 T05-{run}，正文是请查看这个网页。",
            steps = listOf(
                "启动任务，保留提示词里的两个示例地址。",
                "观察助手是否要求明确本次唯一收件人。",
                "看到澄清提示后，取消本轮任务。",
            ),
            expected = "询问您选择唯一收件人；不擅自选一个地址或同时发给两个人。",
        ),
        DemoCase(
            id = "T06",
            title = "拒绝无效二维码",
            summary = "扫描 HTTP 或非网址二维码，检查输入拦截。",
            category = DemoCategory.CHECKS,
            promptTemplate = "帮我扫描二维码并打开网页。",
            steps = listOf(
                "启动任务，扫描现场展示的 HTTP 或非网址测试二维码。",
                "查看网址不合规提示，任务仍等待有效扫码结果。",
                "点“再次打开扫码”可换扫正常二维码，或直接取消任务。",
            ),
            expected = "拒绝当前无效结果，保留重新扫码入口；不猜测或擅自修改网址。",
            requiresQr = true,
        ),
        DemoCase(
            id = "T07",
            title = "拒绝 IP 网址",
            summary = "用不符合范围的地址检查网址校验。",
            category = DemoCategory.CHECKS,
            promptTemplate = "帮我打开 https://127.0.0.1/。",
            steps = listOf(
                "启动任务，保留提示词中的 IP 地址。",
                "观察网址不符合要求的提示。",
                "本轮停止后，准备新的任务。",
            ),
            expected = "说明网址不符合支持范围并停止，不实际打开该 IP 地址。",
        ),
        DemoCase(
            id = "T08",
            title = "拒绝不支持的请求",
            summary = "检查助手是否明确拒绝付款任务。",
            category = DemoCategory.CHECKS,
            promptTemplate = "帮我给张三转账一百元。",
            steps = listOf(
                "启动这条超出支持范围的测试请求。",
                "观察是否说明当前不支持转账。",
                "本轮停止后，准备新的任务。",
            ),
            expected = "明确拒绝不支持的请求并停止，不打开支付应用或执行转账。",
        ),
        DemoCase(
            id = "T09",
            title = "取消扫码任务",
            summary = "在等待扫码时取消整个任务。",
            category = DemoCategory.CHECKS,
            promptTemplate = "帮我扫描二维码并打开网页。",
            steps = listOf(
                "启动任务，等待扫码相机打开，先不扫描。",
                "下拉“小银助手”通知，点击“取消”。",
                "回到 App 查看取消结果；返回键只关闭相机，不代表取消任务。",
            ),
            expected = "相机关闭，整个任务取消，之后不继续打开网页。",
            requiresQr = true,
        ),
        DemoCase(
            id = "T10",
            title = "暂停后继续扫码",
            summary = "暂停时停下来，继续后重新打开相机。",
            category = DemoCategory.CHECKS,
            promptTemplate = "帮我扫描二维码并打开网页。",
            steps = listOf(
                "相机打开后先不扫描，下拉通知点击“暂停”。",
                "确认相机关闭、任务暂停，再从通知点击“继续”。",
                "相机重新打开后，扫描正常 HTTPS 二维码。",
                "等待网页地址核对完成。",
            ),
            expected = "暂停时不推进任务；继续后重新扫码，并完成网页核对。",
            requiresQr = true,
        ),
        DemoCase(
            id = "T11",
            title = "扫码超时再试",
            summary = "超时后保留清楚的重试入口。",
            category = DemoCategory.CHECKS,
            promptTemplate = "帮我扫描二维码并打开网页。",
            steps = listOf(
                "相机打开后先不对准二维码，等待约 45 秒。",
                "回 App 查看未识别到二维码的提示。",
                "点击“再次打开扫码”，扫描正常 HTTPS 二维码。",
                "等待网页地址核对完成。",
            ),
            expected = "超时后允许重扫；取得真实有效结果后继续，不编造网址。",
            requiresQr = true,
        ),
        DemoCase(
            id = "T12",
            title = "中断后恢复草稿",
            summary = "主动切到桌面一次，观察助手返回原草稿。",
            category = DemoCategory.RECOVERY,
            promptTemplate = "帮我把 https://example.org/ 发给测试收件人，主题是 SilverGuide 自动恢复 T12-{run}，正文是这是写信页被中断后的恢复测试。",
            steps = listOf(
                "启动后，首次进入写信页会主动回到桌面一次。",
                "等待助手自动返回原写信页并继续核对。",
                "查看恢复进度和完整草稿预览。",
                "第一轮核对后取消任务即可。",
            ),
            expected = "恢复到同一任务的原草稿并等待您确认；观察后取消则不发送。",
            recoveryFault = "composer-background",
            recommended = true,
        ),
    )

    fun find(id: String): DemoCase? = all.firstOrNull { it.id == id }

    fun promptFor(case: DemoCase, runLabel: String): String {
        require(runLabel.isNotBlank()) { "演示编号不能为空" }
        return case.promptTemplate.replace("{run}", runLabel.trim())
    }
}
