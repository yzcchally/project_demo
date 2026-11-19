package com.example.screenagent

import android.accessibilityservice.AccessibilityService
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

class OperationOverlay(private val service: AccessibilityService) {
    private val windowManager: WindowManager
        get() = service.getSystemService(WindowManager::class.java)
    private var root: View? = null
    private var statusView: TextView? = null
    private var taskView: TextView? = null
    private var countView: TextView? = null
    private var progressList: LinearLayout? = null
    private var progressScroll: ScrollView? = null
    private var detail: View? = null
    private var toggleView: TextView? = null
    private var doneView: TextView? = null
    private var userBlock: View? = null
    private var userInstructionView: TextView? = null
    private var params: WindowManager.LayoutParams? = null
    private var expanded = false

    fun show(): Boolean {
        if (root != null) {
            refresh()
            return true
        }
        return try {
            val layoutParams = WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT,
            ).apply {
                gravity = Gravity.TOP or Gravity.START
                x = screenWidth() - dp(150)
                y = screenHeight() / 3
            }
            val view = build()
            windowManager.addView(view, layoutParams)
            root = view
            params = layoutParams
            refresh()
            true
        } catch (_: RuntimeException) {
            false
        }
    }

    fun hide() {
        val view = root ?: return
        expanded = false
        root = null
        params = null
        try {
            windowManager.removeView(view)
        } catch (_: RuntimeException) {
            // The window is already gone.
        }
    }

    fun expand() {
        expanded = true
        refresh()
    }

    fun presentUserAction() {
        expanded = true
        refresh()
    }

    fun refresh() {
        val paused = RunSession.phase == RunSession.Phase.Paused
        val waiting = RunSession.userInstruction.isNotEmpty()
        statusView?.text = when {
            paused -> "已暂停"
            waiting -> "请操作"
            else -> "操作中"
        }
        statusView?.maxWidth = if (expanded) dp(220) else dp(72)
        taskView?.text = RunSession.task
        toggleView?.text = if (paused) "继续" else "暂停"
        doneView?.visibility = if (waiting) View.VISIBLE else View.GONE
        userInstructionView?.text = RunSession.userInstruction
        userBlock?.visibility = if (waiting) View.VISIBLE else View.GONE
        detail?.visibility = if (expanded) View.VISIBLE else View.GONE
    }

    private fun build(): View {
        val panel = vertical().apply {
            background = card(dp(22).toFloat())
            setPadding(dp(14), dp(10), dp(14), dp(10))
            elevation = dp(8).toFloat()
        }
        val header = LinearLayout(service).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val status = text(size = 15f, bold = true).apply {
            setOnTouchListener(dragOrToggle())
        }
        val done = button(filled = false).apply {
            text = "完成"
            visibility = View.GONE
            setOnClickListener { (service as? ScreenAgentService)?.finishUserAction() }
        }
        val toggle = button(filled = true).apply {
            setOnClickListener { (service as? ScreenAgentService)?.togglePause() }
        }
        header.addView(status)
        header.addView(space(dp(8)))
        header.addView(done)
        header.addView(space(dp(8)))
        header.addView(toggle)
        panel.addView(header)

        val userInstruction = text(size = 14f, color = Color.parseColor("#7A2E0E")).apply {
            maxWidth = dp(206)
        }
        val userBlock = vertical().apply {
            visibility = View.GONE
            setPadding(0, dp(8), 0, 0)
            addView(text(size = 13f, bold = true, color = Color.parseColor("#B54708")).apply {
                this.text = "(请用户进行操作)"
            })
            addView(space(dp(4)))
            addView(frame().apply { addView(userInstruction) })
        }
        panel.addView(userBlock)

        val extra = vertical().apply { visibility = View.GONE }
        val task = text(size = 14f, color = Color.parseColor("#667085")).apply {
            maxWidth = dp(206)
        }
        val taskBox = frame().apply { addView(task) }
        extra.addView(space(dp(8)))
        extra.addView(taskBox)
        val count = text(size = 12f, color = Color.parseColor("#667085")).apply { text = "共 0 条" }
        val steps = vertical()
        val scroller = ScrollView(service).apply {
            isVerticalScrollBarEnabled = true
            isScrollbarFadingEnabled = false
            scrollBarStyle = View.SCROLLBARS_OUTSIDE_INSET
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(148))
            addView(steps)
        }
        val progressBox = frame().apply { addView(scroller) }
        extra.addView(space(dp(10)))
        extra.addView(count)
        extra.addView(space(dp(4)))
        extra.addView(progressBox)
        extra.addView(space(dp(10)))
        val finish = button(filled = false).apply {
            text = "结束"
            setOnClickListener { finish() }
        }
        extra.addView(finish)
        panel.addView(extra)

        statusView = status
        toggleView = toggle
        doneView = done
        this.userBlock = userBlock
        userInstructionView = userInstruction
        taskView = task
        countView = count
        progressList = steps
        progressScroll = scroller
        detail = extra
        return panel
    }

    fun moveTo(x: Int, y: Int): Boolean {
        val layoutParams = params ?: return false
        val view = root ?: return false
        val width = view.width.takeIf { it > 0 } ?: dp(160)
        val height = view.height.takeIf { it > 0 } ?: dp(48)
        layoutParams.x = x.coerceIn(0, (screenWidth() - width).coerceAtLeast(0))
        layoutParams.y = y.coerceIn(0, (screenHeight() - height).coerceAtLeast(0))
        return try {
            windowManager.updateViewLayout(view, layoutParams)
            true
        } catch (_: RuntimeException) {
            false
        }
    }

    fun clearProgress() {
        progressList?.removeAllViews()
        countView?.text = "共 0 条"
    }

    fun addProgress(text: String) {
        val label = text.trim()
        if (label.isEmpty()) return
        val steps = progressList ?: return
        val gray = Color.parseColor("#98A2B3")
        val green = Color.parseColor("#12B76A")
        for (index in 0 until steps.childCount) tintProgress(steps.getChildAt(index), gray)
        steps.addView(progressRow(label, green))
        countView?.text = "共 ${steps.childCount} 条"
        progressScroll?.post { progressScroll?.fullScroll(View.FOCUS_DOWN) }
    }

    private fun dragOrToggle(): View.OnTouchListener {
        var downX = 0f
        var downY = 0f
        var originX = 0
        var originY = 0
        var moved = false
        return View.OnTouchListener { _, event ->
            val layoutParams = params ?: return@OnTouchListener false
            val view = root ?: return@OnTouchListener false
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX
                    downY = event.rawY
                    originX = layoutParams.x
                    originY = layoutParams.y
                    moved = false
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = (event.rawX - downX).toInt()
                    val dy = (event.rawY - downY).toInt()
                    if (kotlin.math.abs(dx) > dp(6) || kotlin.math.abs(dy) > dp(6)) moved = true
                    if (moved) {
                        layoutParams.x = (originX + dx).coerceIn(0, screenWidth() - dp(48))
                        layoutParams.y = (originY + dy).coerceIn(0, screenHeight() - dp(48))
                        windowManager.updateViewLayout(view, layoutParams)
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    if (!moved) {
                        expanded = !expanded
                        refresh()
                    }
                    true
                }
                else -> false
            }
        }
    }

    private fun finish() {
        (service as? ScreenAgentService)?.endSession("button")
    }

    private fun vertical() = LinearLayout(service).apply {
        orientation = LinearLayout.VERTICAL
    }

    private fun text(size: Float, bold: Boolean = false, color: Int = Color.parseColor("#1D2939")) = TextView(service).apply {
        textSize = size
        setTextColor(color)
        if (bold) setTypeface(typeface, Typeface.BOLD)
    }

    private fun button(filled: Boolean) = TextView(service).apply {
        textSize = 15f
        setTextColor(if (filled) Color.WHITE else Color.parseColor("#344054"))
        gravity = Gravity.CENTER
        setPadding(dp(16), dp(8), dp(16), dp(8))
        background = GradientDrawable().apply {
            cornerRadius = dp(18).toFloat()
            if (filled) {
                setColor(Color.parseColor("#175CD3"))
            } else {
                setColor(Color.WHITE)
                setStroke(dp(1), Color.parseColor("#D0D5DD"))
            }
        }
    }

    private fun card(radius: Float) = GradientDrawable().apply {
        setColor(Color.WHITE)
        cornerRadius = radius
    }

    private fun progressRow(label: String, color: Int) = LinearLayout(service).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        setPadding(0, dp(4), dp(4), dp(4))
        addView(View(service).apply {
            layoutParams = LinearLayout.LayoutParams(dp(8), dp(8)).apply { marginEnd = dp(8) }
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(color)
            }
        })
        addView(text(size = 13f, color = color).apply {
            text = label
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        })
    }

    private fun tintProgress(row: View, color: Int) {
        val line = row as? LinearLayout ?: return
        (line.getChildAt(0).background as? GradientDrawable)?.setColor(color)
        (line.getChildAt(1) as? TextView)?.setTextColor(color)
    }

    private fun frame() = vertical().apply {
        background = GradientDrawable().apply {
            setColor(Color.parseColor("#F8F9FB"))
            cornerRadius = dp(10).toFloat()
            setStroke(dp(1), Color.parseColor("#E4E7EC"))
        }
        setPadding(dp(8), dp(6), dp(8), dp(6))
        layoutParams = LinearLayout.LayoutParams(dp(230), LinearLayout.LayoutParams.WRAP_CONTENT)
    }

    private fun space(size: Int) = View(service).apply {
        layoutParams = LinearLayout.LayoutParams(size, size)
    }

    private fun dp(value: Int): Int = (value * service.resources.displayMetrics.density).toInt()

    private fun screenWidth(): Int = service.resources.displayMetrics.widthPixels

    private fun screenHeight(): Int = service.resources.displayMetrics.heightPixels
}
