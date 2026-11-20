package com.example.demo

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.os.SystemClock
import android.util.Log
import android.view.Gravity
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import java.util.UUID

internal enum class PhoneOverlayDestination { Assistant, History, Revision }

internal object PhoneTaskOverlay {
    class Session(val type: PhoneIntentType)

    private data class OpenRequest(
        val destination: PhoneOverlayDestination,
        val token: String,
        val deadline: Long,
    )

    private const val openAction = "com.example.demo.OPEN_PHONE_TASK"
    private const val openToken = "com.example.demo.PHONE_TASK_OPEN_TOKEN"

    var session: Session? = null
        private set
    private var pending: OpenRequest? = null

    fun track(type: PhoneIntentType) {
        if (type == PhoneIntentType.Scan || type == PhoneIntentType.Unsupported) return
        session = Session(type)
        MailAccessibilityService.instance?.refreshPhoneTaskOverlaySoon()
    }

    fun clear(expectedType: PhoneIntentType? = null) {
        if (session == null || (expectedType != null && session?.type != expectedType)) return
        session = null
        MailAccessibilityService.instance?.refreshTaskOverlay()
    }

    fun consumeOpenRequest(intent: Intent?): PhoneOverlayDestination? {
        if (intent?.action != openAction) return null
        val request = pending ?: return null
        if (SystemClock.elapsedRealtime() >= request.deadline) {
            pending = null
            return null
        }
        if (runCatching { intent.getStringExtra(openToken) }.getOrNull() != request.token) return null
        pending = null
        return request.destination
    }

    fun open(context: Context, destination: PhoneOverlayDestination): Boolean {
        val now = SystemClock.elapsedRealtime()
        if (pending?.let { now < it.deadline } == true) return false
        val request = OpenRequest(destination, UUID.randomUUID().toString(), now + 12_000L)
        pending = request
        return try {
            context.startActivity(Intent(context, MainActivity::class.java)
                .setAction(openAction)
                .putExtra(openToken, request.token)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            true
        } catch (_: ActivityNotFoundException) {
            pending = null
            false
        } catch (_: SecurityException) {
            pending = null
            false
        }
    }
}

internal class PhoneTaskOverlayWindow(
    private val service: MailAccessibilityService,
    private val onOpen: (PhoneTaskOverlay.Session, PhoneOverlayDestination) -> Unit,
) {
    private val windows by lazy { service.getSystemService(WindowManager::class.java) }
    private var panel: LinearLayout? = null
    private var shown: PhoneTaskOverlay.Session? = null
    fun matches(session: PhoneTaskOverlay.Session, foreground: String?): Boolean = when (session.type) {
        PhoneIntentType.Scan -> false
        PhoneIntentType.Web -> foreground != null && foreground in browserPackages
        PhoneIntentType.Transit -> foreground == "com.autonavi.minimap"
        PhoneIntentType.Mail -> foreground == "com.huawei.email"
        PhoneIntentType.Family -> foreground != null && foreground in dialerPackages
        PhoneIntentType.WeChat -> foreground == "com.tencent.mm"
        PhoneIntentType.Unsupported -> false
    }

    fun show(session: PhoneTaskOverlay.Session): Boolean {
        if (shown === session && panel != null) return true
        hide()
        val manager = windows ?: return false
        val view = LinearLayout(service).apply {
            orientation = LinearLayout.VERTICAL
            addView(button("返回小银") { onOpen(session, PhoneOverlayDestination.Assistant) })
            addView(button("任务历史") { onOpen(session, PhoneOverlayDestination.History) })
            addView(button("调整任务") { onOpen(session, PhoneOverlayDestination.Revision) })
        }
        val frame = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.END or Gravity.CENTER_VERTICAL
            x = dp(8)
            title = "小银手机任务面板"
        }
        return try {
            manager.addView(view, frame)
            panel = view
            shown = session
            true
        } catch (error: RuntimeException) {
            Log.w("SilverGuidePhoneOverlay", "Could not attach phone task controls: ${error.javaClass.simpleName}")
            false
        }
    }

    fun hide() {
        shown = null
        val view = panel ?: return
        panel = null
        try {
            windows?.removeViewImmediate(view)
        } catch (_: IllegalArgumentException) {
            // The system may already have detached the accessibility window.
        }
    }

    private fun button(label: String, onClick: () -> Unit) = Button(service).apply {
        text = label
        textSize = 17f
        minHeight = dp(48)
        setAllCaps(false)
        setOnClickListener { onClick() }
    }

    private fun dp(value: Int) = (value * service.resources.displayMetrics.density).toInt()

    companion object {
        private val browserPackages = setOf("com.huawei.browser", "com.android.chrome")
        private val dialerPackages = setOf(
            "com.huawei.contacts", "com.huawei.android.contacts", "com.huawei.android.dialer",
            "com.huawei.incallui", "com.android.contacts", "com.android.dialer", "com.android.incallui",
            "com.google.android.dialer", "com.hihonor.contacts", "com.hihonor.incallui",
        )
    }
}
