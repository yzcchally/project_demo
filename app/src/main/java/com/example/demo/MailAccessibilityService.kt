package com.example.demo

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

class MailAccessibilityService : AccessibilityService() {
    override fun onServiceConnected() {
        instance = this
    }

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
