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
    private var promptList: LinearLayout? = null
    private var countView: TextView? = null
    private var progressList: LinearLayout? = null
    private var progressScroll: ScrollView? = null
    private var detail: View? = null
    private var toggleView: TextView? = null
    private var doneView: TextView? = null
    private var reviseView: TextView? = null
    private var userBlock: View? = null
    private var userInstructionView: TextView? = null
    private var captureBlock: View? = null
    private var questionBlock: View? = null
    private var questionView: TextView? = null
    private var warningBlock: View? = null
    private var warningTitleView: TextView? = null
    private var warningBodyView: TextView? = null
    private var continueRiskView: TextView? = null
    private var stopRiskView: TextView? = null
    private var params: WindowManager.LayoutParams? = null
    private var expanded = false
    private var placeX: Int? = null
    private var placeY: Int? = null

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
                x = placeX ?: (screenWidth() - dp(150))
                y = placeY ?: (screenHeight() / 3)
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
        placeX = params?.x
        placeY = params?.y
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

    fun presentWarning() {
        refresh()
    }

    fun presentQuestion() {
        refresh()
    }

    fun refresh() {
        val phase = RunSession.phase
        val paused = phase == RunSession.Phase.Paused
        val revising = phase == RunSession.Phase.Revising
        val waiting = phase == RunSession.Phase.AwaitingUser && RunSession.userInstruction.isNotEmpty()
        val risk = phase == RunSession.Phase.AwaitingRisk
        val refused = phase == RunSession.Phase.Refused
        val asking = phase == RunSession.Phase.AwaitingAnswer
        val capturing = RunSession.capturing
        statusView?.text = when {
            capturing -> "请拍照"
            paused -> "已暂停"
            revising -> "修改中"
            risk -> "有风险"
            refused -> "无法完成"
            asking -> "有问题"
            waiting -> "请操作"
            else -> "操作中"
        }
        statusView?.maxWidth = if (expanded || capturing) dp(220) else dp(72)
        captureBlock?.visibility = if (capturing) View.VISIBLE else View.GONE
        toggleView?.text = if (paused) "继续" else "暂停"
        toggleView?.visibility = if (risk || refused || revising || asking) View.GONE else View.VISIBLE
        reviseView?.visibility = if (paused) View.VISIBLE else View.GONE
        doneView?.visibility = if (waiting) View.VISIBLE else View.GONE
        userInstructionView?.text = RunSession.userInstruction
        userBlock?.visibility = if (waiting) View.VISIBLE else View.GONE
        questionView?.text = RunSession.question
        questionBlock?.visibility = if (asking) View.VISIBLE else View.GONE
        val warning = RunSession.warning.trim()
        val title = if (refused) "很抱歉，这个任务我们无法完成" else "当前页面危险"
        warningTitleView?.text = title
        warningBodyView?.text = warning
        warningBodyView?.visibility = if (warning.isNotEmpty() && warning != title) View.VISIBLE else View.GONE
        continueRiskView?.visibility = if (risk) View.VISIBLE else View.GONE
        stopRiskView?.text = if (risk) "终止结束" else "结束"
        warningBlock?.visibility = if (risk || refused) View.VISIBLE else View.GONE
        detail?.visibility = if (expanded) View.VISIBLE else View.GONE
        renderPrompts()
        renderProgress()
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
        val revise = button(filled = false).apply {
            text = "修改指令"
            textSize = 14f
            visibility = View.GONE
            setPadding(dp(10), dp(8), dp(10), dp(8))
            setOnClickListener { (service as? ScreenAgentService)?.openRevise() }
        }
        header.addView(status)
        header.addView(space(dp(8)))
        header.addView(done)
        header.addView(space(dp(8)))
        header.addView(toggle)
        header.addView(revise)
        (revise.layoutParams as LinearLayout.LayoutParams).marginStart = dp(8)
        panel.addView(header)

        val shoot = button(filled = true).apply {
            text = "拍照"
            textSize = 14f
            setOnClickListener { (service as? ScreenAgentService)?.takePhoto() }
        }
        val capture = vertical().apply {
            visibility = View.GONE
            setPadding(0, dp(8), 0, 0)
            addView(text(size = 13f, bold = true, color = Color.parseColor("#175CD3")).apply {
                this.text = "对准后拍照"
            })
            addView(space(dp(8)))
            addView(shoot)
        }
        panel.addView(capture)

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

        val questionText = text(size = 14f, color = Color.parseColor("#1849A9")).apply {
            maxWidth = dp(220)
        }
        val answer = button(filled = true).apply {
            text = "回答"
            textSize = 14f
            setOnClickListener { (service as? ScreenAgentService)?.openAnswer() }
        }
        val question = vertical().apply {
            visibility = View.GONE
            setPadding(0, dp(8), 0, 0)
            addView(text(size = 13f, bold = true, color = Color.parseColor("#175CD3")).apply {
                this.text = "有问题"
            })
            addView(space(dp(4)))
            addView(frame().apply { addView(questionText) })
            addView(space(dp(8)))
            addView(answer)
        }
        panel.addView(question)

        val warningTitle = text(size = 14f, bold = true, color = Color.parseColor("#B42318")).apply {
            maxWidth = dp(220)
        }
        val warningBody = text(size = 14f, color = Color.parseColor("#7A271A")).apply {
            maxWidth = dp(220)
        }
        val continueRisk = button(filled = true).apply {
            text = "继续操作"
            textSize = 14f
            setOnClickListener { (service as? ScreenAgentService)?.acceptRisk() }
        }
        val stopRisk = button(filled = false).apply {
            text = "终止结束"
            textSize = 14f
            setOnClickListener { (service as? ScreenAgentService)?.endSession("button") }
        }
        val warningActions = LinearLayout(service).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(8), 0, 0)
            addView(continueRisk)
            addView(stopRisk)
            (stopRisk.layoutParams as LinearLayout.LayoutParams).marginStart = dp(8)
        }
        val warning = vertical().apply {
            visibility = View.GONE
            setPadding(0, dp(8), 0, 0)
            addView(warningTitle)
            addView(space(dp(4)))
            addView(frame().apply { addView(warningBody) })
            addView(warningActions)
        }
        panel.addView(warning)

        val extra = vertical().apply { visibility = View.GONE }
        val prompts = vertical().apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            )
        }
        val promptScroller = ScrollView(service).apply {
            isVerticalScrollBarEnabled = true
            isScrollbarFadingEnabled = false
            scrollBarStyle = View.SCROLLBARS_INSIDE_OVERLAY
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(72))
            addView(prompts)
        }
        val promptBox = frame().apply { addView(promptScroller) }
        extra.addView(space(dp(8)))
        extra.addView(sectionLabel("提示"))
        extra.addView(space(dp(6)))
        extra.addView(promptBox)
        val count = text(size = 12f, color = Color.parseColor("#667085")).apply { text = "共 0 条" }
        val steps = vertical()
        val scroller = ScrollView(service).apply {
            isVerticalScrollBarEnabled = true
            isScrollbarFadingEnabled = false
            scrollBarStyle = View.SCROLLBARS_INSIDE_OVERLAY
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(88))
            addView(steps)
        }
        val progressBox = frame().apply { addView(scroller) }
        extra.addView(space(dp(12)))
        extra.addView(sectionLabel("进度"))
        extra.addView(space(dp(4)))
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
        reviseView = revise
        captureBlock = capture
        this.userBlock = userBlock
        userInstructionView = userInstruction
        questionBlock = question
        questionView = questionText
        warningBlock = warning
        warningTitleView = warningTitle
        warningBodyView = warningBody
        continueRiskView = continueRisk
        stopRiskView = stopRisk
        promptList = prompts
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
        renderProgress()
    }

    private fun renderPrompts() {
        val list = promptList ?: return
        list.removeAllViews()
        val task = RunSession.task.trim()
        if (task.isNotEmpty()) {
            list.addView(tagRow("原始", Color.parseColor("#175CD3"), Color.parseColor("#EFF4FF"), task))
        }
        RunSession.additions.forEach { item ->
            val body = item.text.trim()
            if (body.isEmpty()) return@forEach
            val asking = body.startsWith("问：")
            list.addView(tagRow(
                "第${item.progress}步",
                if (asking) Color.parseColor("#027A48") else Color.parseColor("#B54708"),
                if (asking) Color.parseColor("#E7F6EE") else Color.parseColor("#FFF6ED"),
                body,
            ))
        }
    }

    private fun renderProgress() {
        val steps = progressList ?: return
        steps.removeAllViews()
        val items = RunSession.progress
        val gray = Color.parseColor("#98A2B3")
        val green = Color.parseColor("#12B76A")
        items.forEachIndexed { index, item ->
            val latest = index == items.lastIndex
            steps.addView(tagRow(
                "第${index + 1}步",
                if (latest) green else gray,
                if (latest) Color.parseColor("#E7F6EE") else Color.parseColor("#F2F4F7"),
                item.text,
                if (latest) Color.parseColor("#027A48") else Color.parseColor("#667085"),
            ))
        }
        countView?.text = "共 ${items.size} 条"
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

    private fun tagRow(tag: String, tagColor: Int, tagFill: Int, body: String, bodyColor: Int = Color.parseColor("#1D2939")) = LinearLayout(service).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.TOP
        setPadding(0, dp(3), dp(2), dp(3))
        addView(text(size = 11f, bold = true, color = tagColor).apply {
            text = tag
            setPadding(dp(6), dp(2), dp(6), dp(2))
            background = GradientDrawable().apply {
                cornerRadius = dp(8).toFloat()
                setColor(tagFill)
            }
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { marginEnd = dp(8) }
        })
        addView(text(size = 13f, color = bodyColor).apply {
            text = body
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        })
    }

    private fun sectionLabel(value: String) = text(size = 12f, bold = true, color = Color.parseColor("#98A2B3")).apply {
        text = value
    }

    private fun frame() = vertical().apply {
        background = GradientDrawable().apply {
            setColor(Color.parseColor("#F8F9FB"))
            cornerRadius = dp(10).toFloat()
            setStroke(dp(1), Color.parseColor("#E4E7EC"))
        }
        setPadding(dp(8), dp(6), dp(8), dp(6))
        layoutParams = LinearLayout.LayoutParams(dp(248), LinearLayout.LayoutParams.WRAP_CONTENT)
    }

    private fun space(size: Int) = View(service).apply {
        layoutParams = LinearLayout.LayoutParams(size, size)
    }

    private fun dp(value: Int): Int = (value * service.resources.displayMetrics.density).toInt()

    private fun screenWidth(): Int = service.resources.displayMetrics.widthPixels

    private fun screenHeight(): Int = service.resources.displayMetrics.heightPixels
}
