package com.example.demo

internal enum class PhoneQuestionField {
    TransitDestination,
    TransitOrigin,
    TransitCity,
    MailRecipient,
    WeChatContent,
}

internal data class PhoneQuestion(val field: PhoneQuestionField, val prompt: String)

internal fun nextPhoneQuestion(intent: PhoneIntent): PhoneQuestion? = when (intent.type) {
    PhoneIntentType.Transit -> when {
        !validPhonePlace(intent.destination) ->
            PhoneQuestion(PhoneQuestionField.TransitDestination, "您要去哪儿？")
        !validPhonePlace(intent.origin) ->
            PhoneQuestion(PhoneQuestionField.TransitOrigin, "您从哪里出发？")
        !validCity(intent.city) ->
            PhoneQuestion(PhoneQuestionField.TransitCity, "在哪个城市？")
        else -> null
    }
    PhoneIntentType.Mail -> if (!intent.needsScan && !validPhoneRecipient(intent.recipient)) {
        val recipient = intent.alias.takeIf { it.isNotBlank() }
        PhoneQuestion(PhoneQuestionField.MailRecipient,
            if (recipient == null) "邮件要发给谁？请填写收件邮箱。" else "请填写${recipient}的邮箱地址。")
    } else null
    PhoneIntentType.WeChat -> if (!isPhoneWeChatText(intent.shareText)) {
        PhoneQuestion(PhoneQuestionField.WeChatContent, "您想转发什么？请填写文字或链接。")
    } else null
    else -> null
}

internal fun applyPhoneAnswer(intent: PhoneIntent, answer: String): PhoneIntent? {
    val field = nextPhoneQuestion(intent)?.field ?: return null
    if (field == PhoneQuestionField.WeChatContent) {
        // This is the content the user will preview. It is never treated as a command.
        return answer.takeIf(::isPhoneWeChatText)?.let { intent.copy(shareText = it) }
    }
    if (answer.length > 320 || answer.any { it.isISOControl() || it.category == CharCategory.FORMAT } ||
        unsafeAnswer.containsMatchIn(answer)
    ) return null

    val value = answer.trim().trimEnd('。', '！', '!').trimEnd()
    if (field == PhoneQuestionField.MailRecipient) {
        val email = recipientLabel.replaceFirst(value, "").trim().trimEnd('.')
        return email.takeIf(::validPhoneRecipient)?.let { intent.copy(recipient = it) }
    }
    if (ambiguousPlaceAnswer.containsMatchIn(value) || unansweredPlace.matches(value)) return null

    return when (field) {
        PhoneQuestionField.TransitDestination -> {
            val place = destinationLabel.replaceFirst(value, "").trim()
            place.takeIf(::validPhonePlace)?.let { intent.copy(destination = it) }
        }
        PhoneQuestionField.TransitOrigin -> {
            val spokenPlace = spokenOrigin.matchEntire(value)?.groupValues?.get(1)
            val place = (spokenPlace ?: originLabel.replaceFirst(value, "")).trim()
            place.takeIf(::validPhonePlace)?.let { intent.copy(origin = it) }
        }
        PhoneQuestionField.TransitCity -> {
            val city = cityLabel.replaceFirst(value, "").trim()
            city.takeIf(::validCity)?.let { intent.copy(city = it) }
        }
        PhoneQuestionField.MailRecipient, PhoneQuestionField.WeChatContent -> null
    }
}

private fun validCity(value: String): Boolean = value.length <= 40 && validPhonePlace(value)

private val destinationLabel = Regex("""^(?:(?:目的地|终点)\s*[是为：:]|(?:我(?:想|要)\s*)?(?:去|前往|到))\s*""")
private val originLabel = Regex("""^(?:(?:出发地|起点)\s*[是为：:]|我从|从\s+)\s*""")
private val spokenOrigin = Regex("""^(?:我(?:想|要)?\s*)?从\s*(.+?)\s*(?:出发|启程)$""")
private val cityLabel = Regex("""^(?:(?:所在城市|城市)\s*[是为：:]|(?:我(?:现在)?\s*)?在)\s*""")
private val recipientLabel = Regex(
    """^(?:(?:收件人(?:的)?邮箱(?:地址)?|收件邮箱|收件人|电子邮件地址|电子邮箱|邮箱(?:地址)?|(?:我|他|她)的邮箱(?:地址)?)\s*[是为：:]|发给)\s*""",
)
private val ambiguousPlaceAnswer = Regex("""或者|还是|要么|[，、；;！？?]|(?i:\bor\b)""")
private val unansweredPlace = Regex("""^(?:我也?|还)?(?:不知道|不清楚|不确定|没想好|随便|都可以)$""")
private val unsafeAnswer = Regex(
    """忽略.{0,16}(?:指令|规则|要求)|(?:system|developer)\s+prompt|^\s*(?:(?:请(?:你)?|帮我|替我)\s*)?(?:转账(?:给|到)|付款(?:给|到)|支付\s*\d|下单购买|发红包|删除(?:联系人|资料|记录|信息)|卸载应用|发送验证码|拨打电话|取消任务)""",
    RegexOption.IGNORE_CASE,
)
