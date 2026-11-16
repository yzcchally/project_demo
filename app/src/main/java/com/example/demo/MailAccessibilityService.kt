package com.example.demo

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.util.Xml
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo

internal class TransitBridgeExpired : RuntimeException()

/** Shared by the worker and watchdog; a late native return cannot start a write. */
internal class TransitBridgeLease(private val deadline: Long) {
    @Volatile private var cancelled = false
    private val readStartedAt = android.os.SystemClock.elapsedRealtime()
    @Volatile private var readStage = "queued"
    @Volatile private var readNodeCount = 0
    fun cancel() { cancelled = true }
    fun active(): Boolean = !cancelled && android.os.SystemClock.elapsedRealtime() < deadline
    fun check() { if (!active()) throw TransitBridgeExpired() }
    fun readStage(stage: String) { readStage = stage }
    fun readNode() { readNodeCount += 1 }
    /** No nonce, UI strings, addresses, input values or API credentials. */
    fun logReadTiming() {
        android.util.Log.d("SilverGuideTransit", "stage=$readStage nodes=$readNodeCount elapsed_ms=${android.os.SystemClock.elapsedRealtime() - readStartedAt}")
    }
}

class MailAccessibilityService : AccessibilityService() {
    var lastTransitReadReason = "not_requested"
        private set
    var lastTransitInputReason = "not_requested"
        private set
    private var transitRootReason = "root_unavailable"
    private class TransitTreeException(val reason: String) : RuntimeException()

    /** The IME may own activeRoot; never read Amap behind another application. */
    @Suppress("DEPRECATION")
    private fun currentAmapRoot(lease: TransitBridgeLease): AccessibilityNodeInfo? {
        lease.check()
        transitRootReason = "root_unavailable"
        try { configureTransitAccess() } catch (_: Exception) { lease.check(); return null }
        lease.check()
        val active = try { rootInActiveWindow } catch (_: Exception) { null }
        if (active != null) {
            try {
                lease.check()
                if (active.packageName?.toString() == "com.autonavi.minimap") {
                    transitRootReason = "ok"
                    return active
                }
            } catch (error: Exception) { active.recycle(); throw error }
            active.recycle()
        }
        lease.check()
        val entries = try { windows.orEmpty() } catch (_: Exception) { lease.check(); return null }
        try {
            lease.check()
            // Select the front application before checking its package.
            val front = entries.filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
                .maxByOrNull { it.layer } ?: return null
            if (!front.isFocused && !front.isActive) return null
            val root = front.root ?: return null
            try {
                lease.check()
                if (root.packageName?.toString() != "com.autonavi.minimap") {
                    root.recycle(); transitRootReason = "root_package"; return null
                }
                transitRootReason = "ok"
                return root
            } catch (error: Exception) { root.recycle(); throw error }
        } catch (error: TransitBridgeExpired) { throw error }
        catch (_: Exception) { return null }
        finally { entries.forEach { it.recycle() } }
    }

    override fun onServiceConnected() {
        configureTransitAccess()
        instance = this
    }

    private fun configureTransitAccess() {
        val info = serviceInfo ?: return
        val flags = info.flags or AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS or
            AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS or
            AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
        if (flags != info.flags) {
            info.flags = flags
            serviceInfo = info
        }
    }

    private fun nodeBounds(rect: Rect) = "[${rect.left},${rect.top}][${rect.right},${rect.bottom}]"

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        if (instance === this) {
            instance = null
        }
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        if (instance === this) {
            instance = null
        }
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    fun goHome(): Boolean = performGlobalAction(GLOBAL_ACTION_HOME)

    fun tap(x: Float, y: Float): Boolean {
        val path = Path().apply {
            moveTo(x, y)
            lineTo(x + 1f, y + 1f)
        }
        return dispatch(path, 80)
    }

    fun pasteText(value: String): Boolean {
        val root = rootInActiveWindow ?: return false
        val target = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: findEditable(root)
        if (target == null) {
            root.recycle()
            return false
        }
        val wrote = target.performAction(
            AccessibilityNodeInfo.ACTION_SET_TEXT,
            Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value)
            },
        )
        if (target !== root) {
            target.recycle()
        }
        root.recycle()
        return wrote
    }

    fun setMailField(field: String, value: String, useClipboard: Boolean): Boolean {
        if (field !in setOf("to", "subject", "body")) return false
        val root = rootInActiveWindow ?: return false
        if (root.packageName?.toString() != "com.huawei.email") {
            root.recycle()
            return false
        }
        val target = findMailField(root, field)
        if (target == null) {
            root.recycle()
            return false
        }
        target.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        val wrote = if (useClipboard) {
            val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.setPrimaryClip(ClipData.newPlainText("SilverGuide 邮件正文", value))
            target.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, Bundle().apply {
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, 0)
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, target.text?.length ?: 0)
            })
            target.performAction(AccessibilityNodeInfo.ACTION_PASTE)
        } else {
            target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value)
            })
        }
        if (target !== root) target.recycle()
        root.recycle()
        return wrote
    }

    /** Only the exact, freshly observed Amap search field may receive route text. */
    @Suppress("DEPRECATION")
    internal fun setTransitField(field: String, value: String, resourceId: String, bounds: String,
                        expectedText: String, useClipboard: Boolean, lease: TransitBridgeLease): Boolean {
        lastTransitInputReason = "input_invalid"
        if (field !in setOf("query", "origin", "destination") || value.isBlank() || value.length > 200 || bounds.isBlank()) return false
        val root = try { currentAmapRoot(lease) } catch (_: TransitBridgeExpired) {
            lastTransitInputReason = "bridge_timeout"; return false
        } ?: run { lastTransitInputReason = transitRootReason; return false }
        val matches = mutableListOf<AccessibilityNodeInfo>()
        try {
            lease.check()
            if (!root.refresh()) { lease.check(); lastTransitInputReason = "tree_changed"; return false }
            lease.check()
            var count = 0
            fun visit(node: AccessibilityNodeInfo, depth: Int) {
                lease.check()
                if (depth > 40 || count >= 2500) throw TransitTreeException("tree_limit")
                count += 1
                val rect = Rect(); node.getBoundsInScreen(rect)
                if (node.packageName?.toString() == "com.autonavi.minimap" && node.isEditable &&
                    node.isVisibleToUser && node.isEnabled && !node.isPassword &&
                    node.viewIdResourceName.orEmpty() == resourceId && nodeBounds(rect) == bounds &&
                    node.text?.toString().orEmpty() == expectedText) matches.add(AccessibilityNodeInfo.obtain(node))
                for (index in 0 until node.childCount) {
                    lease.check()
                    val child = node.getChild(index) ?: throw TransitTreeException("tree_changed")
                    try { lease.check(); visit(child, depth + 1) } finally { child.recycle() }
                }
            }
            visit(root, 0)
            lease.check()
            if (matches.size != 1) {
                lastTransitInputReason = if (matches.isEmpty()) "field_match_0" else "field_match_multiple"
                return false
            }
            val current = currentAmapRoot(lease) ?: run { lastTransitInputReason = "tree_changed"; return false }
            try {
                if (current.windowId != root.windowId) { lastTransitInputReason = "tree_changed"; return false }
            } finally { if (current !== root) current.recycle() }
            val target = matches.single()
            lease.check()
            target.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
            lease.check()
            val wrote = if (useClipboard) {
                val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                lease.check()
                clipboard.setPrimaryClip(ClipData.newPlainText("SilverGuide 路线地点", value))
                lease.check()
                target.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, Bundle().apply {
                    putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, 0)
                    putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, target.text?.length ?: 0)
                })
                lease.check()
                target.performAction(AccessibilityNodeInfo.ACTION_PASTE)
            } else {
                lease.check()
                target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value)
                })
            }
            lease.check()
            lastTransitInputReason = if (wrote) "ok" else "write_false"
            return wrote
        } catch (_: TransitBridgeExpired) {
            lastTransitInputReason = "bridge_timeout"; return false
        } catch (error: TransitTreeException) {
            lastTransitInputReason = error.reason; return false
        } catch (_: Exception) {
            lastTransitInputReason = "input_error"; return false
        } finally {
            matches.forEach { it.recycle() }
            root.recycle()
        }
    }

    var lastDingtalkReadReason = "not_requested"
        private set

    /** Select the front application window; never inspect DingTalk behind another app. */
    @Suppress("DEPRECATION")
    private fun currentDingtalkRoot(lease: TransitBridgeLease): AccessibilityNodeInfo? {
        lease.check()
        try { configureTransitAccess() } catch (_: Exception) { return null }
        val entries = try { windows.orEmpty() } catch (_: Exception) { return null }
        try {
            lease.check()
            val front = entries.filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
                .maxByOrNull { it.layer } ?: return null
            if (!front.isFocused && !front.isActive) return null
            // A higher third-party accessibility/application overlay makes the target ambiguous.
            if (entries.any { it.layer > front.layer && (it.type == AccessibilityWindowInfo.TYPE_ACCESSIBILITY_OVERLAY ||
                    (it.type == AccessibilityWindowInfo.TYPE_SYSTEM && (it.isFocused || it.isActive))) }) return null
            val root = front.root ?: return null
            if (root.packageName?.toString() != "com.alibaba.android.rimet") { root.recycle(); return null }
            return root
        } finally { entries.forEach { it.recycle() } }
    }

    /** A fresh bounded DingTalk tree, never a cached tree or another app's window. */
    @Suppress("DEPRECATION")
    internal fun dumpDingtalkUi(nonce: String, lease: TransitBridgeLease): String? {
        lastDingtalkReadReason = "nonce_invalid"
        if (!Regex("[A-Za-z0-9_-]{16,80}").matches(nonce)) return null
        lease.readStage("initial_root")
        val root = try { currentDingtalkRoot(lease) } catch (_: TransitBridgeExpired) {
            lastDingtalkReadReason = "bridge_timeout"; return null
        } ?: run { lastDingtalkReadReason = "root_unavailable"; return null }
        try {
            lease.check()
            lease.readStage("root_refresh")
            if (!root.refresh()) { lease.check(); lastDingtalkReadReason = "tree_changed"; return null }
            lease.check()
            val windowId = root.windowId
            val output = StringBuilder()
            val writer = object : java.io.Writer() {
                override fun write(buffer: CharArray, offset: Int, length: Int) {
                    lease.check()
                    if (output.length + length > 500_000) throw TransitTreeException("tree_limit")
                    for (index in offset until offset + length) output.append(buffer[index])
                }
                override fun flush() = Unit
                override fun close() = Unit
            }
            fun xmlText(value: CharSequence?): String {
                val raw = value?.toString().orEmpty()
                if (raw.length > 500_000) throw TransitTreeException("tree_limit")
                val clean = StringBuilder(raw.length)
                var index = 0
                while (index < raw.length) {
                    if (index % 1024 == 0) lease.check()
                    val code = Character.codePointAt(raw, index)
                    if (code == 9 || code == 10 || code == 13 || code in 0x20..0xD7FF ||
                        code in 0xE000..0xFFFD || code in 0x10000..0x10FFFF) clean.appendCodePoint(code)
                    index += Character.charCount(code)
                }
                return clean.toString()
            }
            val xml = Xml.newSerializer()
            xml.setOutput(writer)
            xml.startDocument("UTF-8", true)
            xml.startTag(null, "hierarchy")
            xml.attribute(null, "nonce", nonce)
            xml.attribute(null, "window-id", windowId.toString())
            xml.attribute(null, "captured-at", android.os.SystemClock.elapsedRealtime().toString())
            var count = 0
            fun node(value: AccessibilityNodeInfo, depth: Int) {
                lease.check()
                if (depth > 40 || count >= 2500) throw TransitTreeException("tree_limit")
                if (value.isPassword) throw TransitTreeException("sensitive_field")
                val packageName = value.packageName?.toString().orEmpty()
                if (packageName.isNotEmpty() && packageName != "com.alibaba.android.rimet") throw TransitTreeException("foreign_node")
                count += 1
                lease.readNode()
                lease.readStage("node_attributes")
                val visible = value.isVisibleToUser
                val rect = Rect(); value.getBoundsInScreen(rect)
                xml.startTag(null, "node")
                val attributes = linkedMapOf(
                    "text" to xmlText(value.text), "content-desc" to xmlText(value.contentDescription),
                    "resource-id" to xmlText(value.viewIdResourceName), "class" to xmlText(value.className),
                    "package" to packageName, "bounds" to nodeBounds(rect),
                    "visible-to-user" to visible.toString(), "enabled" to value.isEnabled.toString(),
                    "clickable" to value.isClickable.toString(), "focused" to value.isFocused.toString(),
                    "focusable" to value.isFocusable.toString(), "scrollable" to value.isScrollable.toString(),
                    "editable" to value.isEditable.toString(), "checked" to value.isChecked.toString(),
                    "checkable" to value.isCheckable.toString(), "selected" to value.isSelected.toString(),
                )
                attributes.forEach { (key, text) -> xml.attribute(null, key, text) }
                // The boundary node still undergoes password/package checks
                // and is serialized as invisible. Its descendants cannot be
                // action/evidence sources under any backend native guard.
                // Keep all visible nodes, including zero-bounds footer labels.
                if (visible) {
                    for (index in 0 until value.childCount) {
                        lease.check()
                        lease.readStage("get_child")
                        val child = value.getChild(index) ?: throw TransitTreeException("tree_changed")
                        try { lease.check(); node(child, depth + 1) } finally { child.recycle() }
                    }
                }
                xml.endTag(null, "node")
            }
            node(root, 0)
            xml.endTag(null, "hierarchy")
            xml.endDocument(); xml.flush()
            lease.readStage("verify_window")
            val current = currentDingtalkRoot(lease) ?: run { lastDingtalkReadReason = "tree_changed"; return null }
            try {
                if (current.windowId != windowId) { lastDingtalkReadReason = "tree_changed"; return null }
            } finally { if (current !== root) current.recycle() }
            lease.check()
            lease.readStage("size_check")
            val result = output.toString()
            // Ordered-broadcast result data shares Binder's transaction budget.
            if (result.toByteArray(Charsets.UTF_8).size > 350_000) { lastDingtalkReadReason = "tree_limit"; return null }
            lease.check()
            lastDingtalkReadReason = "ok"
            lease.readStage("complete")
            return result
        } catch (_: TransitBridgeExpired) {
            lastDingtalkReadReason = "bridge_timeout"; return null
        } catch (error: TransitTreeException) {
            lastDingtalkReadReason = error.reason; return null
        } catch (_: Exception) {
            lastDingtalkReadReason = "tree_error"
            return null
        } finally { root.recycle() }
    }

    /** A fresh bounded Amap tree, never a cached tree or another app's window. */
    @Suppress("DEPRECATION")
    internal fun dumpTransitUi(nonce: String, lease: TransitBridgeLease): String? {
        lastTransitReadReason = "nonce_invalid"
        if (!Regex("[A-Za-z0-9_-]{16,80}").matches(nonce)) return null
        lease.readStage("initial_root")
        val root = try { currentAmapRoot(lease) } catch (_: TransitBridgeExpired) {
            lastTransitReadReason = "bridge_timeout"; return null
        } ?: run { lastTransitReadReason = transitRootReason; return null }
        try {
            lease.check()
            lease.readStage("root_refresh")
            if (!root.refresh()) { lease.check(); lastTransitReadReason = "tree_changed"; return null }
            lease.check()
            val windowId = root.windowId
            val output = StringBuilder()
            val writer = object : java.io.Writer() {
                override fun write(buffer: CharArray, offset: Int, length: Int) {
                    lease.check()
                    if (output.length + length > 500_000) throw TransitTreeException("tree_limit")
                    for (index in offset until offset + length) output.append(buffer[index])
                }
                override fun flush() = Unit
                override fun close() = Unit
            }
            fun xmlText(value: CharSequence?): String {
                val raw = value?.toString().orEmpty()
                if (raw.length > 500_000) throw TransitTreeException("tree_limit")
                val clean = StringBuilder(raw.length)
                var index = 0
                while (index < raw.length) {
                    if (index % 1024 == 0) lease.check()
                    val code = Character.codePointAt(raw, index)
                    if (code == 9 || code == 10 || code == 13 || code in 0x20..0xD7FF ||
                        code in 0xE000..0xFFFD || code in 0x10000..0x10FFFF) clean.appendCodePoint(code)
                    index += Character.charCount(code)
                }
                return clean.toString()
            }
            val xml = Xml.newSerializer()
            xml.setOutput(writer)
            xml.startDocument("UTF-8", true)
            xml.startTag(null, "hierarchy")
            xml.attribute(null, "nonce", nonce)
            xml.attribute(null, "window-id", windowId.toString())
            xml.attribute(null, "captured-at", android.os.SystemClock.elapsedRealtime().toString())
            var count = 0
            fun node(value: AccessibilityNodeInfo, depth: Int) {
                lease.check()
                if (depth > 40 || count >= 2500) throw TransitTreeException("tree_limit")
                if (value.isPassword) throw TransitTreeException("sensitive_field")
                val packageName = value.packageName?.toString().orEmpty()
                if (packageName.isNotEmpty() && packageName != "com.autonavi.minimap") throw TransitTreeException("foreign_node")
                count += 1
                lease.readNode()
                lease.readStage("node_attributes")
                val visible = value.isVisibleToUser
                val rect = Rect(); value.getBoundsInScreen(rect)
                xml.startTag(null, "node")
                val attributes = linkedMapOf(
                    "text" to xmlText(value.text), "content-desc" to xmlText(value.contentDescription),
                    "resource-id" to xmlText(value.viewIdResourceName), "class" to xmlText(value.className),
                    "package" to packageName, "bounds" to nodeBounds(rect),
                    "visible-to-user" to visible.toString(), "enabled" to value.isEnabled.toString(),
                    "clickable" to value.isClickable.toString(), "focused" to value.isFocused.toString(),
                    "focusable" to value.isFocusable.toString(), "scrollable" to value.isScrollable.toString(),
                    "editable" to value.isEditable.toString(), "checked" to value.isChecked.toString(),
                    "checkable" to value.isCheckable.toString(), "selected" to value.isSelected.toString(),
                )
                attributes.forEach { (key, text) -> xml.attribute(null, key, text) }
                // The boundary node still undergoes password/package checks
                // and is serialized as invisible. Its descendants cannot be
                // action/evidence sources under any backend native guard.
                // Keep all visible nodes, including zero-bounds footer labels.
                if (visible) {
                    for (index in 0 until value.childCount) {
                        lease.check()
                        lease.readStage("get_child")
                        val child = value.getChild(index) ?: throw TransitTreeException("tree_changed")
                        try { lease.check(); node(child, depth + 1) } finally { child.recycle() }
                    }
                }
                xml.endTag(null, "node")
            }
            node(root, 0)
            xml.endTag(null, "hierarchy")
            xml.endDocument(); xml.flush()
            lease.readStage("verify_window")
            val current = currentAmapRoot(lease) ?: run { lastTransitReadReason = "tree_changed"; return null }
            try {
                if (current.windowId != windowId) { lastTransitReadReason = "tree_changed"; return null }
            } finally { if (current !== root) current.recycle() }
            lease.check()
            lease.readStage("size_check")
            val result = output.toString()
            // Ordered-broadcast result data shares Binder's transaction budget.
            if (result.toByteArray(Charsets.UTF_8).size > 350_000) { lastTransitReadReason = "tree_limit"; return null }
            lease.check()
            lastTransitReadReason = "ok"
            lease.readStage("complete")
            return result
        } catch (_: TransitBridgeExpired) {
            lastTransitReadReason = "bridge_timeout"; return null
        } catch (error: TransitTreeException) {
            lastTransitReadReason = error.reason; return null
        } catch (_: Exception) {
            lastTransitReadReason = "tree_error"
            return null
        } finally { root.recycle() }
    }

    private fun findMailField(node: AccessibilityNodeInfo, field: String, insideTo: Boolean = false): AccessibilityNodeInfo? {
        val rid = node.viewIdResourceName.orEmpty()
        val inTo = insideTo || rid == "com.huawei.email:id/to_content"
        val matches = when (field) {
            "to" -> inTo && node.isEditable && node.className?.contains("AutoComplete") == true
            "subject" -> rid == "com.huawei.email:id/subject"
            "body" -> rid == "main" && node.isEditable
            else -> false
        }
        if (matches) return node
        for (index in 0 until node.childCount) {
            val child = node.getChild(index) ?: continue
            val found = findMailField(child, field, inTo)
            if (found != null) {
                if (found !== child) child.recycle()
                return found
            }
            child.recycle()
        }
        return null
    }

    fun swipeDown(): Boolean {
        val metrics = resources.displayMetrics
        val x = metrics.widthPixels / 2f
        val startY = metrics.heightPixels * 0.30f
        val endY = metrics.heightPixels * 0.58f
        val path = Path().apply {
            moveTo(x, startY)
            lineTo(x, endY)
        }
        return dispatch(path, 400)
    }

    private fun findEditable(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isEditable) {
            return node
        }
        for (index in 0 until node.childCount) {
            val child = node.getChild(index) ?: continue
            val found = findEditable(child)
            if (found != null) {
                if (found !== child) {
                    child.recycle()
                }
                return found
            }
            child.recycle()
        }
        return null
    }

    private fun dispatch(path: Path, durationMs: Long): Boolean {
        val stroke = GestureDescription.StrokeDescription(path, 0, durationMs)
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        return dispatchGesture(gesture, null, null)
    }

    companion object {
        var instance: MailAccessibilityService? = null
            private set
    }
}
