package com.example.demo

import android.app.KeyguardManager
import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import java.net.URI
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

internal enum class PhoneBrowserVerification { Verified, Unavailable }

private const val HUAWEI_BROWSER = "com.huawei.browser"
private const val CHROME_BROWSER = "com.android.chrome"

internal suspend fun verifyPhoneBrowserAddress(
    expectedUrl: String,
    revealAddress: Boolean = false,
): PhoneBrowserVerification {
    if (!isPhonePublicHttps(expectedUrl)) return PhoneBrowserVerification.Unavailable
    val service = withContext(Dispatchers.Main.immediate) { MailAccessibilityService.instance }
        ?: return PhoneBrowserVerification.Unavailable
    return withContext(Dispatchers.IO) {
        var previousWindow: Int? = null
        var revealAttempted = false
        repeat(16) {
            delay(400)
            val sample = try {
                observeBrowser(service, expectedUrl, revealAddress && !revealAttempted)
            } catch (_: RuntimeException) {
                BrowserSample()
            }
            revealAttempted = revealAttempted || sample.revealAttempted
            if (sample.verifiedWindow != null && sample.verifiedWindow == previousWindow) {
                return@withContext PhoneBrowserVerification.Verified
            }
            previousWindow = sample.verifiedWindow
        }
        PhoneBrowserVerification.Unavailable
    }
}

internal fun matchesPhoneBrowserAddress(expectedUrl: String, visibleAddresses: Set<String>): Boolean =
    isPhonePublicHttps(expectedUrl) && !expectedUrl.contains('…') && !expectedUrl.endsWith("...") &&
        visibleAddresses.filter {
            it.startsWith("https://", ignoreCase = true) || it.startsWith("http://", ignoreCase = true)
        }.toSet() == setOf(expectedUrl)

private data class BrowserSample(val verifiedWindow: Int? = null, val revealAttempted: Boolean = false)

private fun foregroundBrowser(windows: List<AccessibilityWindowInfo>): AccessibilityWindowInfo? {
    val front = windows.filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
        .maxByOrNull { it.layer } ?: return null
    if (!front.isFocused && !front.isActive) return null
    if (windows.any {
            it.layer > front.layer && it.type != AccessibilityWindowInfo.TYPE_APPLICATION &&
                it.type != AccessibilityWindowInfo.TYPE_INPUT_METHOD && (it.isFocused || it.isActive)
        }) return null
    return front
}

@Suppress("DEPRECATION")
private fun observeBrowser(
    service: MailAccessibilityService,
    expectedUrl: String,
    mayRevealAddress: Boolean,
): BrowserSample {
    val keyguard = service.getSystemService(KeyguardManager::class.java)
    if (keyguard?.isKeyguardLocked != false) return BrowserSample()
    val windows = service.windows
    return try {
        val front = foregroundBrowser(windows) ?: return BrowserSample()
        val root = front.root ?: return BrowserSample()
        try {
            val browser = root.packageName?.toString()
            if (browser != HUAWEI_BROWSER && browser != CHROME_BROWSER) return BrowserSample()
            val bounds = Rect().also(front::getBoundsInScreen)
            val tree = BrowserToolbar(browser, front.id, bounds, URI(expectedUrl).host)
            try {
                if (!tree.visit(root, 0) || tree.loading) return BrowserSample()
                if (matchesPhoneBrowserAddress(expectedUrl, tree.addresses)) return BrowserSample(front.id)
                if (mayRevealAddress && tree.canReveal()) {
                    // Refresh the one native control and validate the focused window immediately before clicking.
                    val latest = service.windows
                    try {
                        val target = tree.revealTargets.single()
                        val current = foregroundBrowser(latest)
                        if (!keyguard.isKeyguardLocked && current?.id == front.id && current.layer == front.layer &&
                            target.refresh() && tree.isRevealTarget(target)
                        ) {
                            try {
                                target.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                            } catch (_: RuntimeException) {
                                // A failed Binder round trip cannot establish whether the click was delivered.
                            }
                            return BrowserSample(revealAttempted = true)
                        }
                    } finally {
                        latest.forEach { it.recycle() }
                    }
                }
                BrowserSample()
            } finally {
                tree.revealTargets.forEach { it.recycle() }
            }
        } finally {
            root.recycle()
        }
    } finally {
        windows.forEach { it.recycle() }
    }
}

@Suppress("DEPRECATION")
private class BrowserToolbar(
    private val browser: String,
    private val windowId: Int,
    private val windowBounds: Rect,
    private val expectedHost: String,
) {
    val addresses = mutableSetOf<String>()
    val revealTargets = mutableListOf<AccessibilityNodeInfo>()
    var loading = false
        private set
    private var webViewVisible = false
    private var remaining = 800

    fun visit(node: AccessibilityNodeInfo, depth: Int, insideCurrentAddress: Boolean = false): Boolean {
        if (remaining-- <= 0 || depth > 35) return false
        if (node.windowId != windowId || node.packageName?.toString() != browser) return true
        val type = node.className?.toString().orEmpty()
        if (type.endsWith("WebView")) {
            if (onScreen(node)) webViewVisible = true
            return true
        }
        val id = node.viewIdResourceName
        if (onScreen(node)) {
            when (id) {
                "$browser:id/location_bar_progress_bar", "$browser:id/progress" ->
                    if (type.endsWith("ProgressBar")) loading = true
                "$browser:id/url_bar" -> {
                    // The focused edit field may hold text the user has typed but never submitted.
                    if (!node.isFocused) addAddress(node)
                    if (isRevealTarget(node)) revealTargets.add(AccessibilityNodeInfo.obtain(node))
                }
                "$HUAWEI_BROWSER:id/tv_search_web_url" -> {
                    if (insideCurrentAddress && !node.isEditable && type == "android.widget.TextView") addAddress(node)
                }
            }
        }
        val inside = insideCurrentAddress || id == "$HUAWEI_BROWSER:id/search_copy_bar"
        for (index in 0 until node.childCount) {
            val child = node.getChild(index) ?: continue
            try {
                if (!visit(child, depth + 1, inside)) return false
            } finally {
                child.recycle()
            }
        }
        return true
    }

    fun canReveal(): Boolean = browser == HUAWEI_BROWSER && webViewVisible && revealTargets.size == 1

    fun isRevealTarget(node: AccessibilityNodeInfo): Boolean =
        browser == HUAWEI_BROWSER && node.windowId == windowId && node.packageName?.toString() == browser &&
            node.viewIdResourceName == "$HUAWEI_BROWSER:id/url_bar" &&
            node.isEditable && node.isEnabled && node.isClickable && !node.isFocused && onScreen(node) &&
            node.text?.toString().let { it.equals(expectedHost, ignoreCase = true) ||
                it.equals(expectedHost.removePrefix("www."), ignoreCase = true) }

    private fun onScreen(node: AccessibilityNodeInfo): Boolean {
        if (!node.isVisibleToUser) return false
        val bounds = Rect().also(node::getBoundsInScreen)
        return !bounds.isEmpty && windowBounds.contains(bounds.centerX(), bounds.centerY())
    }

    private fun addAddress(node: AccessibilityNodeInfo) {
        node.text?.toString()?.let(addresses::add)
    }
}
