package com.example.demo

private const val revisionInputLimit = 1500

private val publicTransitPlace = Regex(
    """[\p{IsHan}A-Za-z·（）() \-]{1,56}(?:车站|火车站|地铁站|站|机场|航站楼|码头|博物馆|纪念馆|图书馆|展览馆|文化馆|体育馆|体育场|体育中心|公园|广场|景区|动物园|植物园|艺术中心|大学|医院|塔)""",
)
private val privatePlaceCue = Regex("""我家|他家|她家|家里|家庭|住址|住处|小区|住宅|公寓|宿舍|单元|门牌|栋|幢|楼层|电话|邮箱|收件""")
private val cityName = Regex("""[\p{IsHan}]{2,24}""")
private val directTransitCorrection = Regex(
    """^(?:请\s*(?:帮我\s*)?|帮我\s*)?(?:把\s*)?(出发地|出发点|起点|目的地|终点|所在城市|城市)\s*(?:改成|改为|改到|换成|换为|更正为|设置为|设为)\s*[:：]?\s*(.+)$""",
)
private val multipleTransitFields = Regex("""[，,、；;]|(?:出发地|出发点|起点|目的地|终点|所在城市|城市)\s*(?:改|换|更正|设置|设为)""")

internal fun phoneTaskRevisionPrompt(current: PhoneIntent?, correction: String): String {
    val words = correction.trim().take(revisionInputLimit)
    if (current == null || words.isBlank()) return words
    val task = when (current.type) {
        PhoneIntentType.Transit -> "公交地铁"
        PhoneIntentType.Mail -> "发邮件"
        PhoneIntentType.Family -> "联系家人"
        PhoneIntentType.WeChat -> "微信转发"
        PhoneIntentType.Scan -> "扫码"
        PhoneIntentType.Web -> "打开网页"
        PhoneIntentType.Unsupported -> return words
    }
    val context = buildString {
        appendLine("用户正在修改当前任务。当前任务类型：$task。")
        if (current.type == PhoneIntentType.Transit) {
            current.city.trim().takeIf { cityName.matches(it) && !privatePlaceCue.containsMatchIn(it) }
                ?.let { appendLine("当前城市：$it") }
            current.origin.takeIf(::isPublicTransitContext)?.let { appendLine("已知出发地：$it") }
            current.destination.takeIf(::isPublicTransitContext)?.let { appendLine("已知目的地：$it") }
        }
        appendLine("上下文仅用于理解任务类型。只提取用户本次更正明确说出的字段。")
        append("用户本次更正：")
    }
    return if (context.length + words.length <= revisionInputLimit) context + words else words
}

private fun isPublicTransitContext(value: String): Boolean =
    publicTransitPlace.matches(value) && !privatePlaceCue.containsMatchIn(value)

internal fun revisedPhoneIntent(current: PhoneIntent?, interpreted: PhoneIntent): PhoneIntent? {
    if (interpreted.type == PhoneIntentType.Unsupported) return null
    if (interpreted.type != PhoneIntentType.Transit) return interpreted
    if ((interpreted.origin.isNotBlank() && !validPhonePlace(interpreted.origin)) ||
        (interpreted.destination.isNotBlank() && !validPhonePlace(interpreted.destination)) ||
        (interpreted.city.isNotBlank() && !validRevisionCity(interpreted.city))
    ) return null
    if (current?.type != PhoneIntentType.Transit) return interpreted

    return interpreted.copy(
        origin = interpreted.origin.ifBlank { current.origin.takeIf(::validPhonePlace).orEmpty() },
        destination = interpreted.destination.ifBlank { current.destination.takeIf(::validPhonePlace).orEmpty() },
        city = interpreted.city.ifBlank { current.city.takeIf(::validRevisionCity).orEmpty() },
    )
}

internal fun directPhoneTaskRevision(current: PhoneIntent?, correction: String): PhoneIntent? {
    if (current?.type != PhoneIntentType.Transit) return null
    if (correction.length > 320 || correction.any { it.isISOControl() || it.category == CharCategory.FORMAT }) return null
    val match = directTransitCorrection.matchEntire(correction.trim()) ?: return null
    val field = match.groupValues[1]
    val value = match.groupValues[2].trim()
    if (multipleTransitFields.containsMatchIn(value)) return null

    // The clarification validator selects the first missing transit field.
    // Temporary values identify the field here and are never applied to the task.
    val validation = when (field) {
        "出发地", "出发点", "起点" -> PhoneIntent(PhoneIntentType.Transit, destination = "车站")
        "目的地", "终点" -> PhoneIntent(PhoneIntentType.Transit)
        else -> PhoneIntent(PhoneIntentType.Transit, origin = "车站", destination = "车站")
    }
    val answer = applyPhoneAnswer(validation, value) ?: return null
    return when (field) {
        "出发地", "出发点", "起点" -> current.copy(origin = answer.origin)
        "目的地", "终点" -> current.copy(destination = answer.destination)
        else -> current.copy(city = answer.city)
    }
}

private fun validRevisionCity(value: String): Boolean = value.length <= 40 && validPhonePlace(value)
