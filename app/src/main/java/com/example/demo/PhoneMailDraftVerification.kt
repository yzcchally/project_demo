package com.example.demo

import android.app.KeyguardManager
import android.os.Build
import android.os.SystemClock
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext

internal enum class PhoneMailDraftVerification { Verified, Unavailable }

private const val huaweiMailPackage = "com.huawei.email"
private const val mailId = "$huaweiMailPackage:id/"
private const val draftReadTimeMs = 4_000L
private val draftAddress = Regex("""[A-Za-z0-9.!#\x24%&'*+/=?^_\x60{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}""")

internal fun phoneMailDraftBody(intent: PhoneIntent): String =
    listOf(intent.body, intent.url.takeUnless { it in intent.body }.orEmpty())
        .filter(String::isNotBlank).joinToString("\n\n")

/** Only called after an explicit successful launch of this draft. */
internal suspend fun verifyPhoneMailDraft(intent: PhoneIntent): PhoneMailDraftVerification {
    val unavailable = PhoneMailDraftVerification.Unavailable
    if (intent.type != PhoneIntentType.Mail || intent.needsScan ||
        !draftAddress.matches(intent.recipient) ||
        (intent.url.isNotEmpty() && !isPhonePublicHttps(intent.url))
    ) return unavailable
    val service = withContext(Dispatchers.Main.immediate) { MailAccessibilityService.instance }
        ?: return unavailable
    val expectedSubject = normalizeDraftContent(intent.subject)
    val expectedBody = normalizeDraftContent(phoneMailDraftBody(intent))
    return withContext(Dispatchers.IO) {
        val deadline = SystemClock.elapsedRealtime() + draftReadTimeMs
        while (SystemClock.elapsedRealtime() < deadline) {
            currentCoroutineContext().ensureActive()
            val draft = readFrontHuaweiMail(service) { root ->
                if (root.refresh()) readHuaweiDraft(root, deadline) else null
            }
            if (draft != null && SystemClock.elapsedRealtime() < deadline &&
                draft.recipients == setOf(intent.recipient) &&
                draft.subject == expectedSubject && draft.body == expectedBody &&
                readFrontHuaweiMail(service) { root -> root.windowId } == draft.windowId &&
                withContext(Dispatchers.Main.immediate) { MailAccessibilityService.instance === service }
            ) return@withContext PhoneMailDraftVerification.Verified
            delay(250)
        }
        unavailable
    }
}

@Suppress("DEPRECATION")
private fun <T> readFrontHuaweiMail(
    service: MailAccessibilityService,
    read: (AccessibilityNodeInfo) -> T?,
): T? {
    if (service.getSystemService(KeyguardManager::class.java)?.isKeyguardLocked != false) return null
    val windows = try {
        service.windows.orEmpty()
    } catch (_: RuntimeException) {
        return null
    }
    try {
        val applications = windows.filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
        val front = applications.maxByOrNull { it.layer } ?: return null
        if (applications.count { it.layer == front.layer } != 1 || (!front.isFocused && !front.isActive)) return null
        if (windows.any {
                it.layer > front.layer && ((it.type == AccessibilityWindowInfo.TYPE_ACCESSIBILITY_OVERLAY &&
                    !isOwnPhoneTaskPanel(it, service.packageName)) ||
                    (it.type == AccessibilityWindowInfo.TYPE_SYSTEM && (it.isFocused || it.isActive)))
            }
        ) return null
        val root = front.root ?: return null
        try {
            if (root.packageName?.toString() != huaweiMailPackage) return null
            return read(root)
        } finally {
            root.recycle()
        }
    } catch (_: RuntimeException) {
        return null
    } finally {
        windows.forEach { it.recycle() }
    }
}

@Suppress("DEPRECATION")
private fun isOwnPhoneTaskPanel(window: AccessibilityWindowInfo, ownPackage: String): Boolean {
    if (window.title?.toString() != "小银手机任务面板") return false
    val root = window.root ?: return false
    return try { root.packageName?.toString() == ownPackage } finally { root.recycle() }
}

private data class HuaweiDraft(
    val windowId: Int,
    val recipients: Set<String>,
    val subject: String,
    val body: String,
)

private enum class AddressArea { To, Other }

@Suppress("DEPRECATION")
private fun readHuaweiDraft(root: AccessibilityNodeInfo, deadline: Long): HuaweiDraft? {
    var visited = 0
    var composerButtons = 0
    var toContainers = 0
    val subject = mutableListOf<String>()
    val body = mutableListOf<String>()
    val recipients = mutableSetOf<String>()

    fun visit(node: AccessibilityNodeInfo, area: AddressArea?, depth: Int): Boolean {
        if (++visited > 600 || depth > 35 || SystemClock.elapsedRealtime() >= deadline) return false
        val inMail = node.packageName?.toString() == huaweiMailPackage
        val id = node.viewIdResourceName.orEmpty()
        var currentArea = area
        if (inMail) {
            val text = if (Build.VERSION.SDK_INT >= 26 && node.isShowingHintText) ""
                else normalizeDraftContent(node.text?.toString().orEmpty())
            when (id) {
                mailId + "send_email_btn" -> if (node.isVisibleToUser) composerButtons++
                mailId + "to_content" -> {
                    toContainers++
                    currentArea = AddressArea.To
                }
                mailId + "cc_content", mailId + "bcc_content" -> currentArea = AddressArea.Other
                mailId + "subject" -> subject.add(text)
                "main" -> body.add(text)
            }
            if ("attachment_name" in id && text.isNotBlank()) return false
            if (currentArea != null) {
                val addresses = draftAddress.findAll(text + " " + node.contentDescription?.toString().orEmpty())
                    .map { it.value }.toSet()
                if (node.isEditable && text.isNotBlank() && !draftAddress.matches(text)) return false
                if (currentArea == AddressArea.Other && addresses.isNotEmpty()) return false
                if (currentArea == AddressArea.To) recipients.addAll(addresses)
            }
        }
        for (index in 0 until node.childCount) {
            val child = node.getChild(index) ?: return false
            try {
                if (!visit(child, currentArea, depth + 1)) return false
            } finally {
                child.recycle()
            }
        }
        return true
    }

    if (!visit(root, null, 0) || composerButtons != 1 || toContainers != 1 ||
        subject.size != 1 || body.size != 1
    ) return null
    return HuaweiDraft(root.windowId, recipients, subject.single(), body.single())
}

private fun normalizeDraftContent(value: String): String =
    value.replace("\r\n", "\n").replace('\r', '\n').replace('\u00a0', ' ').trim()
