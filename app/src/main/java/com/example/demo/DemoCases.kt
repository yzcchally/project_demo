package com.example.demo

enum class DemoCategory(val label: String) {
    SUCCESS("正常流程"),
    CHECKS("异常与控制"),
    RECOVERY("异常恢复"),
}

enum class DemoDomain(val title: String, val subtitle: String) {
    MAP("地图查询", "公交地铁路线"),
    MAIL("邮件案例", "扫码与发信"),
    FAMILY("联系家人", "离线拨号"),
}

enum class DemoFilter(val label: String) {
    RECOMMENDED("推荐"), SUCCESS("正常"), CHECKS("异常"), RECOVERY("恢复")
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
    val domain: DemoDomain = DemoDomain.MAIL,
) {
    val startLabel: String get() = when {
        domain == DemoDomain.MAP -> "查询公交地铁路线"
        category == DemoCategory.SUCCESS -> "开始完整演示"
        else -> "让小银完成这个任务"
    }
}

/** Local examples only. Choosing one prepares text; it never starts a task. */
object DemoCases {
    internal const val navigationExample = "在广州，从广州塔地铁站到客村地铁站怎么走？"
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
            promptTemplate = "帮我扫描二维码，把网页链接发给测试收件人，主题是 SilverGuide 自动恢复 T12-{run}，正文是这是写信页被中断后的恢复测试。",
            steps = listOf(
                "启动后先扫描测试二维码；首次进入写信页会主动回到桌面一次。",
                "等待助手自动返回原写信页并继续核对。",
                "查看恢复进度和完整草稿预览。",
                "第一轮核对后取消任务即可。",
            ),
            expected = "恢复到同一任务的原草稿并等待您确认；观察后取消则不发送。",
            requiresQr = true,
            recoveryFault = "composer-background",
            recommended = true,
        ),
        DemoCase(
            id = "T13",
            title = "开始高德公交地铁导航",
            summary = "查询广州塔到客村的地铁路线，核对后点「开始高德导航」并确认。",
            category = DemoCategory.SUCCESS,
            domain = DemoDomain.MAP,
            promptTemplate = navigationExample,
            steps = emptyList(),
            expected = "高德显示「结束导航」，小银结束本次操作并保留高德前台。",
            recommended = true,
        ),
        DemoCase(
            id = "T14",
            title = "当前位置完整查询",
            summary = "开始一次任务，从高德核对真实当前位置，查询前往博物馆的公交地铁路线，再回小银查看步骤和朗读；定位不可用时请您补充起点。",
            category = DemoCategory.SUCCESS,
            domain = DemoDomain.MAP,
            promptTemplate = "在广州市，从当前位置去广东省博物馆，坐地铁或公交，选择高德推荐方案。",
            steps = emptyList(),
            expected = "使用真实位置或停下来澄清，不把演示地址冒充当前位置。",
        ),
        DemoCase(
            id = "T15",
            title = "出发地不明确",
            summary = "没有绑定家庭地址时，检查小银是否先询问起点。",
            category = DemoCategory.CHECKS,
            domain = DemoDomain.MAP,
            promptTemplate = "在广州市，从家里去广东省博物馆，坐地铁或公交。",
            steps = listOf(
                "填入案例并开始，保留提示词中的“家里”。",
                "观察小银是否要求填写明确出发地。",
                "本轮只检查澄清提示，查看后取消任务。",
            ),
            expected = "先询问起点地址，不把“家里”替换成当前位置或演示站点。",
            recommended = true,
        ),
        DemoCase(
            id = "T16",
            title = "目的地不明确",
            summary = "没有绑定家庭地址时，检查小银是否先询问目的地。",
            category = DemoCategory.CHECKS,
            domain = DemoDomain.MAP,
            promptTemplate = "在广州市，从体育西路地铁站去我家，坐地铁或公交。",
            steps = listOf(
                "填入案例并开始，保留提示词中的“我家”。",
                "观察小银是否要求填写明确目的地，并保留已提供的出发地。",
                "本轮只检查缺失信息，查看后取消任务。",
            ),
            expected = "询问目的地地址，不猜测家庭位置，也不擅自选择同名地点。",
        ),
        DemoCase(
            id = "T17",
            title = "取消路线查询",
            summary = "在查询途中取消，检查后续操作和旧结果是否停止。",
            category = DemoCategory.CHECKS,
            domain = DemoDomain.MAP,
            promptTemplate = "在广州市，从广州塔地铁站去公园前地铁站，坐地铁或公交，选择高德推荐方案。",
            steps = listOf(
                "开始任务，等高德打开并进入地点或路线查询。",
                "下拉小银助手通知，点击“取消”。",
                "回到小银查看取消状态；已经发出的调用可能需要等返回后停止。",
                "观察取消后是否不再点击高德、不提交迟到的路线结果。",
            ),
            expected = "任务变为已取消，后续检查点停止操作；不把迟到回包显示为新完成结果。",
        ),
        DemoCase(
            id = "T18",
            title = "补充起点后继续",
            summary = "先触发起点澄清，再在手机补充公共站点，继续同一条路线任务。",
            category = DemoCategory.RECOVERY,
            domain = DemoDomain.MAP,
            promptTemplate = "在广州市，从家里去广东省博物馆，坐地铁或公交，选择高德推荐方案。",
            steps = listOf(
                "开始任务，等待小银询问明确出发地。",
                "在出发地填写“体育西路地铁站”，核对城市和目的地仍为广州市、广东省博物馆。",
                "点击“补充好了，继续查询”，让任务继续打开高德。",
                "回小银核对真实路线、步骤卡片和朗读；本轮使用的是本人补充的明确起点。",
            ),
            expected = "补充信息后继续原任务并重新核对路线；明确起点不会被当作 GPS 定位。",
            recommended = true,
        ),
        DemoCase(
            id = "T19",
            title = "暂停后继续查询",
            summary = "手动暂停和继续，检查旧控件失效后是否重新观察页面。",
            category = DemoCategory.RECOVERY,
            domain = DemoDomain.MAP,
            promptTemplate = "在广州市，从流花地铁站去广州东站地铁站，坐地铁或公交，尽量少换乘。",
            steps = listOf(
                "开始任务，等高德打开后，从小银通知点击“暂停”。",
                "观察暂停状态，等待几秒，保持手机页面不变，暂时不要点按高德。",
                "从通知点击“继续”，等待当前调用返回并重新核对页面。",
                "检查继续后的路线；若页面无法安全恢复，应明确提示重新查询，再按提示重查。",
            ),
            expected = "暂停时停止推进；继续时重新观察，不沿用暂停前控件，无法安全继续时保留明确重查入口。",
        ),
        DemoCase(
            id = "T20",
            title = "恢复网络后重查",
            summary = "需本人手动准备手机断网，观察真实失败提示，恢复网络后再查询。",
            category = DemoCategory.RECOVERY,
            domain = DemoDomain.MAP,
            promptTemplate = "在广州市，从体育西路地铁站去广东省博物馆，坐地铁或公交，选择高德推荐方案。",
            steps = listOf(
                "本人在手机关闭 Wi-Fi 和移动数据；电脑后端保持联网，USB 连接保持正常。",
                "开始任务，观察高德是否出现网络错误；若缓存仍返回结果，说明本次故障未复现。",
                "本人恢复手机网络，确认高德可以正常搜索。",
                "回小银等待旧任务停止，再点击“重新查询”，等待新查询重新读取真实页面。",
                "核对新的查询时间和结果；成功、部分读取或失败均按实际状态记录。",
            ),
            expected = "故障复现时显示真实原因；网络恢复后以新查询重新核对，不把旧结果当成新成功。",
        ),
    )

    fun find(id: String): DemoCase? = all.firstOrNull { it.id == id }

    fun visible(domain: DemoDomain, filter: DemoFilter): List<DemoCase> = all.filter { example ->
        example.domain == domain && when (filter) {
            DemoFilter.RECOMMENDED -> example.recommended
            DemoFilter.SUCCESS -> example.category == DemoCategory.SUCCESS
            DemoFilter.CHECKS -> example.category == DemoCategory.CHECKS
            DemoFilter.RECOVERY -> example.category == DemoCategory.RECOVERY
        }
    }

    fun promptFor(case: DemoCase, runLabel: String): String {
        require(runLabel.isNotBlank()) { "演示编号不能为空" }
        return case.promptTemplate.replace("{run}", runLabel.trim())
    }
}
