package com.example.demo

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import java.net.URI
import java.util.Locale
import org.json.JSONObject

internal enum class PhoneIntentType { Family, Scan, Web, Transit, Mail, WeChat, Unsupported }

internal data class PhoneIntent(
    val type: PhoneIntentType = PhoneIntentType.Unsupported,
    val alias: String = "",
    val url: String = "",
    val origin: String = "",
    val destination: String = "",
    val city: String = "",
    val recipient: String = "",
    val subject: String = "",
    val body: String = "",
    val needsScan: Boolean = false,
    val shareText: String = "",
) : java.io.Serializable

private val phoneUrlInText = Regex("https?://[^\\s<>\"“”‘’。，、；！？）】》]+", RegexOption.IGNORE_CASE)
private const val PHONE_WECHAT_TEXT_LIMIT = 2000
private val otherShareScheme = Regex(
    """(?<![A-Za-z0-9+.-])(?:[a-z][a-z0-9+.-]*://|(?:https?|javascript|data|file|content|intent|mailto|tel|sms|vbscript|weixin|wxp|about|market):|//|\\\\)""",
    RegexOption.IGNORE_CASE,
)
private val bareLocalShareAddress = Regex(
    """(?<![\p{L}\p{N}_.@-])(?:localhost(?=[:/]|$)|(?:\d{1,3}\.){3}\d{1,3}(?![\d.])|\[[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,8}])""",
    RegexOption.IGNORE_CASE,
)
private val weChatContent = Regex("微信(?:文字|消息|内容)\\s*(?:是|为|写|[:：])|(?:转发|分享)内容\\s*(?:是|为|写|[:：])")
private val negatedWeChatShare = Regex(
    "不(?:用微信|转发|分享)|(?:不要|不想|不许|先不|无需|不必|别(?!忘)|取消|禁止|拒绝)[^，。！？；,!?;]{0,20}(?:微信|转发|分享)|" +
        "\\b(?:do\\s+not|don't|never|cancel|stop)\\b[^.!?;]{0,60}\\b(?:wechat|share|forward)\\b",
    RegexOption.IGNORE_CASE,
)
private val domainLabel = Regex("[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
private val numberLabel = Regex("(?:0x[0-9a-f]+|[0-9]+)")
private val privateSuffixes = setOf(
    "local", "localhost", "localdomain", "internal", "intranet", "lan", "home", "corp",
    "onion", "invalid", "test", "example", "alt", "arpa",
)
private val phoneEmailInText = Regex(
    "(?<![A-Za-z0-9._%+@-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,63}(?![A-Za-z0-9_@-]|\\.[A-Za-z0-9])",
)
private val mailContent = Regex("(?:主题|标题|正文|内容)\\s*(?:是|为|写|[:：])")
private val familyCue = Regex(
    "联系|通(?:个|一下|一通)?(?:话|电话)|打(?:个|一通)?电话|打给|拨(?:个)?(?:电话|号|打|给|过去)|说说话|聊(?:聊|天|几句|两句)|\\bcall\\b",
    RegexOption.IGNORE_CASE,
)
private val scanCue = Regex("扫一扫|扫描|扫码|扫[^，。！？；,!?;]{0,8}码|二维码|方(?:块|形)?码|\\bQR\\b", RegexOption.IGNORE_CASE)
private val scanAction = Regex(
    "扫一扫|扫描|扫码|扫[^，。！？；,!?;]{0,8}码|(?:识别|读出).{0,20}(?:二维码|方块码|黑白格)|" +
        "(?:二维码|方块码|黑白格)[^，。！？；,!?;]{0,12}(?:扫(?:一下|出来|一遍)|识别|读出)|\\bscan\\b",
    RegexOption.IGNORE_CASE,
)
private val transitCue = Regex(
    "地铁|公交|轻轨|巴士|公共交通|公共汽车|路线|从.{1,100}(?:去|到|前往)|怎么(?:去|走|到|坐车|搭车)|想去|要去|前往|^去|去.{1,80}的路|\\b(?:bus|subway|metro|transit|route)\\b",
    RegexOption.IGNORE_CASE,
)
private val mailCue = Regex("邮件|电邮|写信|(?:写|发|寄)(?:一)?封信|\\b(?:email|e-mail|mail)\\b", RegexOption.IGNORE_CASE)
private val otherAction = Regex(
    "转账|付款|支付|下单|买票|订票|退票|发红包|发短信|发送验证码|删除|卸载|清空|登录|录像|录音|拍照|开车|驾车|打车|报(?:警|火警)|" +
        "(?:制作|生成).{0,15}(?:二维码|方块码)|(?:打开|关闭|启用|关掉).{0,6}(?:设置|蓝牙|定位|飞行模式)",
)
private val negatedAction = Regex(
    "(?:不要|不想|不许|先不|不用|无需|不必|别(?!忘)|取消|禁止|拒绝)[^，。！？；,!?;]{0,24}" +
        "(?:扫|打开|网址|网页|链接|电话|通话|拨|联系|地铁|公交|查.{0,4}路线|邮件|写信|发送|发给|去了?|前往)|" +
        "不(?:扫|打开|打电话|拨号|联系|查路线|发邮件|写信)|" +
        "\\b(?:do\\s+not|don't|never|cancel|stop)\\b[^.!?;]{0,60}\\b(?:call|dial|scan|open|visit|browse|mail|email|transit|route)\\b",
    RegexOption.IGNORE_CASE,
)
private val promptOverride = Regex("忽略.{0,16}(?:指令|规则|要求)|(?:仅|只)输出|返回.{0,10}JSON|(?:system|developer) prompt", RegexOption.IGNORE_CASE)
private val alternative = Regex("或者|还是|要么|\\bor\\b", RegexOption.IGNORE_CASE)

internal fun isPhonePublicHttps(raw: String): Boolean {
    if (raw.length !in 1..2048 || raw.any { it == '\\' || it.isWhitespace() || it.isISOControl() || it.category == CharCategory.FORMAT }) return false
    val uri = runCatching { URI(raw) }.getOrNull() ?: return false
    if (!uri.scheme.equals("https", ignoreCase = true) || uri.rawUserInfo != null || uri.port !in setOf(-1, 443)) return false
    val sourceHost = uri.host ?: return false
    if (uri.rawAuthority != sourceHost && uri.rawAuthority != "$sourceHost:443") return false
    val host = sourceHost.lowercase(Locale.ROOT)
    val labels = host.split('.')
    return host.length <= 253 && labels.size >= 2 && labels.all(domainLabel::matches) && labels.last() !in privateSuffixes &&
        !labels.all(numberLabel::matches) && !labels.last().all(Char::isDigit)
}

private fun literalPhoneUrls(text: String): Sequence<String> =
    phoneUrlInText.findAll(text).map { it.value.trimEnd('.', ',', ';', '!', ')', '}', ']') }

internal fun isPhoneWeChatText(text: String): Boolean {
    if (text.length !in 1..PHONE_WECHAT_TEXT_LIMIT || text.isBlank() ||
        text.any { it.isISOControl() || it.category == CharCategory.FORMAT }) return false
    if (literalPhoneUrls(text).any { !isPhonePublicHttps(it) }) return false
    val withoutWebLinks = phoneUrlInText.replace(text, " ")
    return !otherShareScheme.containsMatchIn(withoutWebLinks) && !bareLocalShareAddress.containsMatchIn(withoutWebLinks)
}

internal fun parsePhoneIntent(result: JSONObject, instruction: String): PhoneIntent {
    val unsupported = PhoneIntent()
    if (instruction.isBlank() || promptOverride.containsMatchIn(instruction)) return unsupported
    val type = when (result.opt("type")) {
        "call_family" -> PhoneIntentType.Family
        "scan_qr" -> PhoneIntentType.Scan
        "open_url" -> PhoneIntentType.Web
        "plan_transit" -> PhoneIntentType.Transit
        "compose_email" -> PhoneIntentType.Mail
        "share_wechat" -> PhoneIntentType.WeChat
        else -> return unsupported
    }
    fun field(key: String): String = (result.opt(key) as? String).orEmpty().trim()
    fun grounded(key: String, limit: Int, multiline: Boolean = false): String {
        val value = field(key)
        return value.takeIf {
            it.length in 1..limit && instruction.contains(it) && it.none { character ->
                character.category == CharCategory.FORMAT || (character.isISOControl() && !(multiline && character == '\n'))
            }
        }.orEmpty()
    }

    // Separate explicitly labeled content from the request before looking for other actions.
    val headerEnd = when (type) {
        PhoneIntentType.Mail -> mailContent.find(instruction)?.range?.first
        PhoneIntentType.WeChat -> weChatContent.find(instruction)?.range?.first
        else -> null
    }
    val header = instruction.take(headerEnd ?: instruction.length)
    val headerWithoutUrls = phoneUrlInText.replace(header, " ")
    val command = phoneEmailInText.replace(headerWithoutUrls, " ")
    if (negatedAction.containsMatchIn(command) || otherAction.containsMatchIn(command)) return unsupported

    val family = familyCue.containsMatchIn(command)
    val scan = scanCue.containsMatchIn(command)
    val transit = transitCue.containsMatchIn(command)
    val mail = mailCue.containsMatchIn(command)
    val urls = literalPhoneUrls(instruction).distinct().toList()
    val explicitUrl = urls.singleOrNull()?.takeIf(::isPhonePublicHttps).orEmpty()
    val proposedUrl = field("url")

    return when (type) {
        PhoneIntentType.Family -> {
            val alias = grounded("alias", 40)
            if (scan || transit || mail || urls.isNotEmpty() || (field("alias").isNotEmpty() && alias.isEmpty()) ||
                alternative.containsMatchIn(command)) unsupported
            else PhoneIntent(type, alias = alias)
        }
        PhoneIntentType.Scan -> {
            if (!family && !transit && !mail && urls.isEmpty()) PhoneIntent(type) else unsupported
        }
        PhoneIntentType.Web -> {
            if (!family && !scan && !transit && !mail && explicitUrl.isNotEmpty() &&
                (proposedUrl.isEmpty() || proposedUrl == explicitUrl)) PhoneIntent(type, url = explicitUrl)
            else unsupported
        }
        PhoneIntentType.Transit -> {
            val origin = grounded("origin", 120)
            val destination = grounded("destination", 120)
            val city = grounded("city", 40)
            if (scan || family || mail || urls.isNotEmpty()) unsupported
            else PhoneIntent(type, origin = origin.takeIf(::validPhonePlace).orEmpty(),
                destination = destination.takeIf(::validPhonePlace).orEmpty(),
                city = city.takeIf(::validPhonePlace).orEmpty())
        }
        PhoneIntentType.Mail -> {
            val scanSource = scanAction.containsMatchIn(command)
            val needsScan = result.opt("needs_scan") == true
            val recipients = phoneEmailInText.findAll(headerWithoutUrls).map { it.value }.distinct().toList()
            val recipient = recipients.singleOrNull()?.takeIf(::validPhoneRecipient).orEmpty()
            val subject = grounded("subject", 160)
            val body = grounded("body", 2000, multiline = true)
            if (family || transit || scanSource != needsScan || (needsScan && urls.isNotEmpty()) ||
                recipients.size > 1 || (recipients.isNotEmpty() && recipient.isEmpty())) unsupported
            else PhoneIntent(type, alias = grounded("alias", 40), url = explicitUrl, recipient = recipient,
                subject = subject, body = body, needsScan = needsScan)
        }
        PhoneIntentType.WeChat -> {
            if (family || scan || transit || mail || negatedWeChatShare.containsMatchIn(command)) unsupported
            else {
                val safeSourceUrls = urls.takeUnless {
                    otherShareScheme.containsMatchIn(phoneUrlInText.replace(instruction, " "))
                }.orEmpty()
                val text = grounded("share_text", PHONE_WECHAT_TEXT_LIMIT).takeIf {
                    isPhoneWeChatText(it) && literalPhoneUrls(it).all(safeSourceUrls::contains)
                }.orEmpty()
                val explicitShareUrl = explicitUrl.takeIf {
                    field("share_text").isEmpty() && it in safeSourceUrls && (proposedUrl.isEmpty() || proposedUrl == it)
                }.orEmpty()
                PhoneIntent(type, shareText = text.ifEmpty { explicitShareUrl })
            }
        }
        PhoneIntentType.Unsupported -> unsupported
    }
}

internal fun validPhonePlace(value: String): Boolean = value.length in 1..120 && value.isNotBlank() && value.none {
    it in "|&=?#%\\/" || it.isISOControl() || it.category == CharCategory.FORMAT
}

internal fun validPhoneRecipient(value: String): Boolean = value.length <= 254 && phoneEmailInText.matches(value) &&
    value.substringBefore('@').let { it.length <= 64 && !it.startsWith('.') && !it.endsWith('.') && ".." !in it } &&
    value.substringAfter('@').lowercase(Locale.ROOT).split('.').all(domainLabel::matches)

internal fun openPhoneWeChat(context: Context, text: String): Boolean {
    if (!isPhoneWeChatText(text)) return false
    val intent = Intent(Intent.ACTION_SEND)
        .setType("text/plain")
        .putExtra(Intent.EXTRA_TEXT, text)
        .setPackage("com.tencent.mm")
    return launchPhoneIntent(context, intent)
}

internal fun openPhoneWeb(context: Context, url: String): Boolean {
    if (!isPhonePublicHttps(url)) return false
    return launchPhoneIntent(context, Intent(Intent.ACTION_VIEW, Uri.parse(url).normalizeScheme())
        .addCategory(Intent.CATEGORY_BROWSABLE))
}

internal fun openPhoneTransit(context: Context, intent: PhoneIntent): Boolean {
    if (intent.type != PhoneIntentType.Transit || !validPhonePlace(intent.destination) ||
        (intent.city.isNotEmpty() && !validPhonePlace(intent.city))) return false
    val keyword = when {
        intent.city.isEmpty() || intent.destination.startsWith(intent.city) -> intent.destination
        else -> "${intent.city} ${intent.destination}"
    }
    // Native route planning needs resolved coordinates. Open the documented POI picker for the reviewed name.
    val uri = Uri.Builder().scheme("androidamap").authority("poi")
        .appendQueryParameter("sourceApplication", "SilverGuide")
        .appendQueryParameter("keywords", keyword)
        .appendQueryParameter("dev", "0")
        .build()
    return launchPhoneIntent(context, Intent(Intent.ACTION_VIEW, uri).setPackage("com.autonavi.minimap"))
}

internal fun openPhoneMail(context: Context, intent: PhoneIntent): Boolean {
    if (intent.type != PhoneIntentType.Mail || intent.needsScan ||
        (intent.recipient.isNotEmpty() && !validPhoneRecipient(intent.recipient)) ||
        (intent.url.isNotEmpty() && !isPhonePublicHttps(intent.url)) ||
        intent.subject.length > 160 || intent.subject.any { it.isISOControl() || it.category == CharCategory.FORMAT } ||
        intent.body.length > 4000 || intent.body.any { (it.isISOControl() && it != '\n') || it.category == CharCategory.FORMAT }) return false
    val body = phoneMailDraftBody(intent)
    val fields = buildList {
        if (intent.subject.isNotEmpty()) add("subject=${Uri.encode(intent.subject)}")
        if (body.isNotEmpty()) add("body=${Uri.encode(body)}")
    }
    val mailto = buildString {
        append("mailto:")
        append(Uri.encode(intent.recipient, "@"))
        if (fields.isNotEmpty()) append(fields.joinToString("&", prefix = "?"))
    }
    return launchPhoneIntent(context, Intent(Intent.ACTION_SENDTO, Uri.parse(mailto)))
}

private fun launchPhoneIntent(context: Context, intent: Intent): Boolean = try {
    context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    true
} catch (_: ActivityNotFoundException) {
    false
} catch (_: SecurityException) {
    false
}
