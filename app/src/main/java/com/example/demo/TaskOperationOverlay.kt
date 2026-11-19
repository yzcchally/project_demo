package com.example.demo

import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowInsets
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import kotlin.math.abs

/** Adapts screen-agent's draggable, tappable OperationOverlay to this app's task state. */
internal class TaskOperationOverlay(private val service: MailAccessibilityService) {
    private val windows by lazy { service.getSystemService(WindowManager::class.java) }
    private val touchSlop by lazy { ViewConfiguration.get(service).scaledTouchSlop }
    private val accent = Color.rgb(77, 53, 137)
    private val ink = Color.rgb(32, 28, 43)
    private var panel: Panel? = null
    private var snapshot: TaskOverlaySnapshot? = null
    private var expanded = false
    private var savedX: Int? = null
    private var savedY: Int? = null

    private class Panel(
        val view: LinearLayout,
        val frame: WindowManager.LayoutParams,
        val header: Button,
        val details: LinearLayout,
        val steps: LinearLayout,
        val scroll: ScrollView,
        val resume: Button,
    )

    fun show(next: TaskOverlaySnapshot) {
        if (snapshot == next && panel != null) return
        if (snapshot?.taskId != next.taskId) expanded = false
        snapshot = next
        if (panel == null) {
            val (width, height) = availableSize()
            val frame = WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                PixelFormat.TRANSLUCENT,
            ).apply {
                gravity = Gravity.TOP or Gravity.LEFT
                x = savedX ?: (width - dp(133)).coerceAtLeast(0)
                y = savedY ?: height / 3
                title = "小银任务面板"
            }
            val created = build(frame)
            panel = created
            render(created, next)
            try {
                windows.addView(created.view, frame)
            } catch (_: RuntimeException) {
                hide()
            }
        } else {
            panel?.let { render(it, next) }
        }
    }

    fun hide() {
        snapshot = null
        expanded = false
        val previous = panel ?: return
        panel = null
        try {
            windows.removeViewImmediate(previous.view)
        } catch (_: IllegalArgumentException) {
            // The system already detached the window.
        }
    }

    fun configurationChanged() {
        val current = panel ?: return
        sizeDetails(current)
        current.view.post { moveTo(current, current.frame.x, current.frame.y) }
    }

    private fun render(current: Panel, state: TaskOverlaySnapshot) {
        current.header.text = state.phase.title + if (expanded) "  ▴" else "  ▾"
        current.header.contentDescription = state.phase.title + if (expanded) "，收起任务进度" else "，展开任务进度"
        current.details.visibility = if (expanded) View.VISIBLE else View.GONE
        current.resume.visibility = if (state.phase.canResume) View.VISIBLE else View.GONE
        current.steps.removeAllViews()
        for ((index, label) in state.steps.withIndex()) {
            current.steps.addView(TextView(service).apply {
                text = "${index + 1}. $label"
                textSize = 16f
                setTextColor(ink)
                setPadding(dp(3), dp(5), dp(3), dp(5))
            })
        }
    }

    private fun build(frame: WindowManager.LayoutParams): Panel {
        val root = column().apply {
            background = rounded(Color.WHITE, 16, accent)
            setPadding(dp(8), dp(6), dp(8), dp(6))
            elevation = dp(8).toFloat()
        }
        val header = button("", primary = true).apply {
            setOnClickListener {
                val current = panel ?: return@setOnClickListener
                val state = snapshot ?: return@setOnClickListener
                expanded = !expanded
                render(current, state)
            }
            setOnTouchListener(dragOrTap())
        }
        root.addView(header)
        val steps = column()
        val scroll = ScrollView(service).apply {
            isFillViewport = true
            addView(steps)
        }
        val resume = button("继续任务", primary = true).apply {
            setOnClickListener { snapshot?.let { TaskOverlayState.control(it, "resume") } }
        }
        val details = column().apply {
            addView(TextView(service).apply {
                text = "最近进度"
                textSize = 16f
                setTextColor(ink)
                setTypeface(typeface, Typeface.BOLD)
                setPadding(dp(3), dp(8), 0, dp(2))
            })
            addView(scroll)
            addView(button("打开操作记录").apply {
                setOnClickListener { TaskHistoryEntry.open(service) }
            })
            addView(resume)
            addView(button("取消任务").apply {
                setOnClickListener { snapshot?.let { TaskOverlayState.control(it, "cancel") } }
            })
        }
        root.addView(details)
        return Panel(root, frame, header, details, steps, scroll, resume).also { built ->
            sizeDetails(built)
            root.addOnLayoutChangeListener { _, left, top, right, bottom, oldLeft, oldTop, oldRight, oldBottom ->
                if (right - left != oldRight - oldLeft || bottom - top != oldBottom - oldTop) {
                    moveTo(built, frame.x, frame.y)
                }
            }
        }
    }

    private fun dragOrTap(): View.OnTouchListener {
        var downX = 0f
        var downY = 0f
        var startX = 0
        var startY = 0
        var dragging = false
        return View.OnTouchListener { view, event ->
            val current = panel ?: return@OnTouchListener false
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX
                    downY = event.rawY
                    startX = current.frame.x
                    startY = current.frame.y
                    dragging = false
                    view.isPressed = true
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = (event.rawX - downX).toInt()
                    val dy = (event.rawY - downY).toInt()
                    if (abs(dx) > touchSlop || abs(dy) > touchSlop) dragging = true
                    if (dragging) {
                        view.isPressed = false
                        moveTo(current, startX + dx, startY + dy)
                        savedX = current.frame.x
                        savedY = current.frame.y
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    view.isPressed = false
                    if (!dragging) view.performClick()
                    true
                }
                MotionEvent.ACTION_CANCEL -> {
                    view.isPressed = false
                    true
                }
                else -> true
            }
        }
    }

    private fun moveTo(current: Panel, x: Int, y: Int) {
        if (current !== panel || !current.view.isAttachedToWindow) return
        val (width, height) = availableSize()
        val boundedX = x.coerceIn(0, (width - current.view.width).coerceAtLeast(0))
        val boundedY = y.coerceIn(0, (height - current.view.height).coerceAtLeast(0))
        if (current.frame.x == boundedX && current.frame.y == boundedY) return
        current.frame.x = boundedX
        current.frame.y = boundedY
        try {
            windows.updateViewLayout(current.view, current.frame)
        } catch (_: IllegalArgumentException) {
            hide()
        }
    }

    private fun sizeDetails(current: Panel) {
        val (width, height) = availableSize()
        current.details.layoutParams = LinearLayout.LayoutParams(
            minOf(dp(235), (width - dp(16)).coerceAtLeast(1)),
            LinearLayout.LayoutParams.WRAP_CONTENT,
        )
        current.scroll.layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            minOf(dp(126), height / 4).coerceAtLeast(1),
        )
    }

    private fun availableSize(): Pair<Int, Int> {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val metrics = windows.currentWindowMetrics
            val insets = metrics.windowInsets.getInsetsIgnoringVisibility(
                WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout(),
            )
            return (metrics.bounds.width() - insets.left - insets.right) to
                (metrics.bounds.height() - insets.top - insets.bottom)
        }
        val metrics = service.resources.displayMetrics
        return metrics.widthPixels to metrics.heightPixels
    }

    private fun column() = LinearLayout(service).apply { orientation = LinearLayout.VERTICAL }

    private fun button(label: String, primary: Boolean = false) = Button(service).apply {
        text = label
        setAllCaps(false)
        textSize = 16f
        minHeight = dp(48)
        setPadding(dp(9), 0, dp(9), 0)
        setTextColor(if (primary) Color.WHITE else accent)
        background = rounded(if (primary) accent else Color.WHITE, 9, accent)
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { bottomMargin = dp(3) }
    }

    private fun rounded(fill: Int, radius: Int, border: Int) = GradientDrawable().apply {
        setColor(fill)
        setStroke(dp(1), border)
        cornerRadius = dp(radius).toFloat()
    }

    private fun dp(value: Int) = (service.resources.displayMetrics.density * value).toInt()
}
