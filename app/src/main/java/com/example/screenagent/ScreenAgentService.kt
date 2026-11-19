package com.example.screenagent

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Path
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.view.Display
import android.view.accessibility.AccessibilityNodeInfo
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class ScreenAgentService : AccessibilityService() {
    private val main = Handler(Looper.getMainLooper())
    private val screenshots = Executors.newSingleThreadExecutor()
    private val operation = OperationOverlay(this)
    private var socket: WebSocket? = null
    private var pendingTask: String? = null
    private var pendingRecord: String? = null
    private var pendingMode: String? = null

    fun showOverlay(): Boolean = operation.show()

    fun begin(task: String, server: String, recordId: String) {
        RunSession.recordId = recordId
        RunSession.dialogues = emptyList()
        RunSession.lastStep = 0
        RunSession.userInstruction = ""
        RunSession.warning = ""
        RunSession.question = ""
        RunSession.answering = false
        RunSession.capturing = false
        RunSession.progress = emptyList()
        RunSession.additions = emptyList()
        RunSession.latestStep = 0
        operation.clearProgress()
        pendingMode = "start"
        pendingTask = task
        pendingRecord = recordId
        if (socket != null) {
            main.postDelayed({ sendPending() }, 800)
        } else {
            connect(server)
        }
    }

    fun resume(task: String, server: String, recordId: String, lastStep: Int) {
        RunSession.recordId = recordId
        RunSession.userInstruction = ""
        RunSession.warning = ""
        RunSession.question = ""
        RunSession.answering = false
        RunSession.capturing = false
        RunSession.progress = emptyList()
        RunSession.additions = emptyList()
        RunSession.latestStep = lastStep
        operation.clearProgress()
        pendingMode = "resume"
        pendingTask = task
        pendingRecord = recordId
        if (socket != null) {
            main.post { sendPending() }
        } else {
            connect(server)
        }
    }

    fun hideOverlay() = operation.hide()

    fun refreshOverlay() = operation.refresh()

    fun moveOverlay(x: Int, y: Int) = operation.moveTo(x, y)

    fun takePhoto() {
        CaptureBridge.shoot?.invoke()
    }

    fun endSession(source: String, returnToApp: Boolean = true) {
        val pendingCapture = captureCallback
        captureCallback = null
        CaptureBridge.listener = null
        CaptureBridge.shoot = null
        RunSession.capturing = false
        val cancelCapture = CaptureBridge.cancel
        CaptureBridge.cancel = null
        val saved = sendArchive()
        val next = RunSession.dialogues.size + 1
        RunSession.dialogues = RunSession.dialogues + RunSession.Dialogue(
            next,
            RunSession.dialoguePrompt,
            RunSession.additions,
            RunSession.progress,
        )
        RunSession.lastStep = maxOf(RunSession.lastStep, RunSession.latestStep)
        RunSession.userInstruction = ""
        RunSession.warning = ""
        RunSession.question = ""
        RunSession.answering = false
        RunSession.phase = RunSession.Phase.Review
        RunSession.notice = if (saved) "" else "没连上电脑，这次没有保存"
        reportPhase(source, "stopped")
        if (pendingCapture != null) sendStepDone()
        cancelCapture?.invoke()
        operation.hide()
        if (returnToApp) {
            startActivity(Intent(this, MainActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
            })
        }
    }

    override fun onServiceConnected() {
        instance = this
        status = "无障碍已开启"
    }

    override fun onUnbind(intent: Intent?): Boolean {
        if (instance === this) instance = null
        operation.hide()
        disconnect()
        return super.onUnbind(intent)
    }

    override fun onAccessibilityEvent(event: android.view.accessibility.AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_CONNECT) connect(intent.getStringExtra(EXTRA_SERVER).orEmpty())
        if (intent?.action == ACTION_DISCONNECT) disconnect()
        return START_NOT_STICKY
    }

    private fun connect(server: String) {
        val http = if (server.startsWith("http://") || server.startsWith("https://")) server.trimEnd('/') else "http://$server"
        val ws = http.replaceFirst("https://", "wss://").replaceFirst("http://", "ws://") + "/phone"
        disconnect()
        status = "正在连接"
        socket = OkHttpClient().newWebSocket(Request.Builder().url(ws).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                status = "已连接"
                main.postDelayed({ sendPending() }, 800)
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                main.post { reply(webSocket, text) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                status = t.message ?: "连接失败"
                main.post { pushProgress(status, null) }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                status = "已断开"
            }
        })
    }

    private fun disconnect() {
        socket?.close(1000, "bye")
        socket = null
        status = "已断开"
    }

    private fun sendPending() {
        val mode = pendingMode ?: return
        val task = pendingTask ?: return
        val webSocket = socket ?: return
        pendingMode = null
        pendingTask = null
        val message = JSONObject().put("op", mode).put("task", task).put("record", pendingRecord.orEmpty())
        pendingRecord = null
        webSocket.send(message.toString())
    }

    private fun sendArchive(): Boolean {
        val webSocket = socket ?: return false
        val additions = JSONArray()
        RunSession.additions.forEach { item ->
            additions.put(JSONObject().put("progress", item.progress).put("text", item.text))
        }
        val progress = JSONArray()
        RunSession.progress.forEach { item ->
            val row = JSONObject().put("text", item.text)
            if (item.step != null) row.put("step", item.step)
            progress.put(row)
        }
        val message = JSONObject()
            .put("op", "archive")
            .put("task", RunSession.dialoguePrompt)
            .put("record", RunSession.recordId)
            .put("additions", additions)
            .put("progress", progress)
        return webSocket.send(message.toString())
    }

    private fun reply(webSocket: WebSocket, text: String) {
        val request = JSONObject(text)
        if (request.optString("op") == "record_name") {
            val name = request.optString("name").trim()
            if (name.isNotEmpty()) RunSession.recordName = name
            return
        }
        if (request.optString("op") == "result") {
            handleResult(request)
            return
        }
        val result = if (request.optString("op") == "capture") capture() else if (request.optString("op") == "command") {
            execute(request.getJSONObject("command"))
        } else {
            JSONObject().put("ok", false).put("error", "未知请求")
        }
        webSocket.send(result.toString())
    }

    fun reportPhase(source: String? = null, wire: String? = null) {
        val message = JSONObject().put("op", "phase").put("phase", wire ?: phaseName())
        if (!source.isNullOrEmpty()) message.put("source", source)
        socket?.send(message.toString())
    }

    private fun handleResult(message: JSONObject) {
        val command = if (message.isNull("command")) null else message.optJSONObject("command")
        val type = command?.optString("type").orEmpty()
        val step = if (message.has("step") && !message.isNull("step")) message.optInt("step") else null
        if (step != null) RunSession.latestStep = step
        val hold = type == "user_action" || type == "risk" || type == "refuse" || type == "ask"
        if (command != null && type.isNotEmpty() && type != "camera" && type != "scan") execute(command)
        pushProgress(progressText(message), step)
        when (type) {
            "camera" -> beginCapture(CaptureActivity::class.java, keepOverlay = true) { bytes, _ ->
                val extra = JSONObject().put("kind", "photo")
                if (bytes != null && bytes.isNotEmpty()) {
                    extra.put("image_jpeg_base64", Base64.encodeToString(bytes, Base64.NO_WRAP))
                } else {
                    extra.put("error", "没拍到照片")
                }
                sendStepDone(extra)
            }
            "scan" -> beginCapture(QrCaptureActivity::class.java, keepOverlay = false) { _, text ->
                sendStepDone(scanExtra(text))
            }
            else -> if (hold) sendStepDone() else main.postDelayed({ sendStepDone() }, 2000)
        }
    }

    fun togglePause() {
        if (RunSession.phase == RunSession.Phase.AwaitingRisk || RunSession.phase == RunSession.Phase.AwaitingAnswer || RunSession.phase == RunSession.Phase.Refused || RunSession.phase == RunSession.Phase.Revising) return
        val waiting = RunSession.userInstruction.isNotEmpty()
        RunSession.phase = when (RunSession.phase) {
            RunSession.Phase.Paused -> if (waiting) RunSession.Phase.AwaitingUser else RunSession.Phase.Operating
            else -> RunSession.Phase.Paused
        }
        if (RunSession.phase == RunSession.Phase.Paused) operation.expand()
        operation.refresh()
        reportPhase()
    }

    fun finishUserAction() {
        RunSession.userInstruction = ""
        RunSession.phase = RunSession.Phase.Operating
        operation.refresh()
        reportPhase("done")
    }

    fun openRevise() {
        if (RunSession.phase != RunSession.Phase.Paused) return
        RunSession.phase = RunSession.Phase.Revising
        operation.hide()
        reportPhase()
        startActivity(Intent(this, MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
        })
    }

    fun applyRevision(selectedIndex: Int, addition: String) {
        val items = RunSession.progress
        val cutting = items.isNotEmpty() && selectedIndex in 0 until items.lastIndex
        val keepStep = if (cutting) items.take(selectedIndex + 1).mapNotNull { it.step }.maxOrNull() ?: 0 else null
        val progressNo = if (cutting) selectedIndex + 1 else items.size
        if (cutting) {
            RunSession.progress = items.take(progressNo)
            RunSession.latestStep = keepStep ?: 0
            RunSession.additions = RunSession.additions.filter { it.progress <= progressNo }
        }
        val text = addition.trim()
        if (text.isNotEmpty()) {
            RunSession.additions = RunSession.additions + RunSession.AddedPrompt(progressNo, text)
        }
        val message = JSONObject().put("op", "revise").put("addition", text).put("progress", progressNo)
        if (keepStep != null) message.put("keep_step", keepStep)
        socket?.send(message.toString())
        RunSession.userInstruction = ""
        RunSession.warning = ""
        RunSession.phase = RunSession.Phase.Operating
        operation.show()
        reportPhase("revise")
    }

    fun openAnswer() {
        if (RunSession.phase != RunSession.Phase.AwaitingAnswer) return
        RunSession.answering = true
        operation.hide()
        startActivity(Intent(this, MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
        })
    }

    fun submitAnswer(answer: String) {
        val question = RunSession.question.trim()
        val reply = answer.trim()
        if (question.isEmpty() || reply.isEmpty()) return
        val text = "问：$question\n答：$reply"
        val progressNo = RunSession.progress.size
        RunSession.additions = RunSession.additions + RunSession.AddedPrompt(progressNo, text)
        socket?.send(JSONObject()
            .put("op", "answer")
            .put("text", text)
            .put("progress", progressNo)
            .put("step", RunSession.latestStep)
            .toString())
        RunSession.question = ""
        RunSession.answering = false
        RunSession.phase = RunSession.Phase.Operating
        operation.show()
        reportPhase("answer")
    }

    fun acceptRisk() {
        val message = RunSession.warning.trim()
        RunSession.warning = ""
        RunSession.phase = RunSession.Phase.Operating
        operation.refresh()
        if (message.isNotEmpty()) {
            socket?.send(JSONObject().put("op", "note").put("text", "用户已知晓风险并选择继续：$message").toString())
        }
        reportPhase("continue")
    }

    private fun sendStepDone(extra: JSONObject? = null) {
        val message = JSONObject().put("op", "step_done").put("phase", phaseName())
        if (extra != null) message.put("extra", extra)
        socket?.send(message.toString())
    }

    private var captureCallback: ((ByteArray?, String?) -> Unit)? = null

    private fun beginCapture(activity: Class<*>, keepOverlay: Boolean, done: (ByteArray?, String?) -> Unit) {
        fun complete(bytes: ByteArray?, text: String?) {
            val callback = captureCallback ?: return
            captureCallback = null
            CaptureBridge.listener = null
            CaptureBridge.shoot = null
            CaptureBridge.cancel = null
            RunSession.capturing = false
            main.post {
                operation.show()
                operation.refresh()
                callback(bytes, text)
            }
        }
        captureCallback = done
        CaptureBridge.listener = { bytes, text -> complete(bytes, text) }
        if (keepOverlay) {
            RunSession.capturing = true
            operation.show()
            operation.refresh()
        } else {
            RunSession.capturing = false
            operation.hide()
        }
        try {
            startActivity(Intent(this, activity).apply { addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) })
        } catch (_: RuntimeException) {
            complete(null, null)
            return
        }
        main.postDelayed({ complete(null, null) }, 115_000)
    }

    private fun scanExtra(text: String?): JSONObject {
        val value = text?.trim().orEmpty()
        if (value.isEmpty()) return JSONObject().put("kind", "url").put("error", "没有扫到码")
        if (value.startsWith("http://") || value.startsWith("https://")) {
            return JSONObject().put("kind", "url").put("text", value).put("opened", openUrl(value))
        }
        return JSONObject().put("kind", "text").put("text", value)
    }

    private fun openUrl(value: String): Boolean {
        return try {
            startActivity(Intent(Intent.ACTION_VIEW, android.net.Uri.parse(value)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            true
        } catch (_: Exception) {
            false
        }
    }

    private fun progressText(message: JSONObject): String {
        if (!message.has("progress") || message.isNull("progress")) return ""
        val value = message.optString("progress").trim()
        return if (value == "null") "" else value
    }

    private fun pushProgress(text: String, step: Int?) {
        val label = text.trim()
        if (label.isEmpty() || label == "null") return
        RunSession.progress = RunSession.progress + RunSession.ProgressItem(label, step)
        if (step != null) RunSession.latestStep = step
        operation.refresh()
    }

    private fun phaseName(): String {
        return when (RunSession.phase) {
            RunSession.Phase.Paused -> "paused"
            RunSession.Phase.Revising -> "revising"
            RunSession.Phase.AwaitingUser -> "awaiting_user"
            RunSession.Phase.AwaitingRisk -> "awaiting_risk"
            RunSession.Phase.AwaitingAnswer -> "awaiting_answer"
            RunSession.Phase.Refused -> "refused"
            RunSession.Phase.Review -> "review"
            RunSession.Phase.Input -> "stopped"
            RunSession.Phase.Operating -> "operating"
        }
    }

    private fun capture(): JSONObject {
        val latch = CountDownLatch(1)
        var body = JSONObject().put("ok", false).put("error", "截图失败")
        takeScreenshot(Display.DEFAULT_DISPLAY, screenshots, object : TakeScreenshotCallback {
            override fun onSuccess(screenshot: ScreenshotResult) {
                try {
                    screenshot.hardwareBuffer.use { buffer ->
                        val hardware = Bitmap.wrapHardwareBuffer(buffer, screenshot.colorSpace)
                        if (hardware != null) {
                            val soft = hardware.copy(Bitmap.Config.ARGB_8888, false)
                            hardware.recycle()
                            val stream = ByteArrayOutputStream()
                            soft.compress(Bitmap.CompressFormat.PNG, 100, stream)
                            body = JSONObject()
                                .put("ok", true)
                                .put("width", soft.width)
                                .put("height", soft.height)
                                .put("image_png_base64", Base64.encodeToString(stream.toByteArray(), Base64.NO_WRAP))
                            soft.recycle()
                        }
                    }
                } finally {
                    latch.countDown()
                }
            }

            override fun onFailure(errorCode: Int) {
                body = JSONObject().put("ok", false).put("error", "截图失败")
                latch.countDown()
            }
        })
        latch.await(5, TimeUnit.SECONDS)
        return body
    }

    private fun execute(command: JSONObject): JSONObject {
        val type = command.optString("type")
        val ok = if (type == "tap") gesture(command.getInt("x").toFloat(), command.getInt("y").toFloat(), command.getInt("x") + 1f, command.getInt("y") + 1f, 50)
        else if (type == "long_press") gesture(command.getInt("x").toFloat(), command.getInt("y").toFloat(), command.getInt("x") + 1f, command.getInt("y") + 1f, 600)
        else if (type == "swipe") gesture(command.getInt("x1").toFloat(), command.getInt("y1").toFloat(), command.getInt("x2").toFloat(), command.getInt("y2").toFloat(), command.getLong("duration_ms"))
        else if (type == "zoom") zoom(command.getInt("x").toFloat(), command.getInt("y").toFloat(), command.getString("direction"), command.getInt("distance").toFloat())
        else if (type == "input_text") inputAt(command.getInt("x").toFloat(), command.getInt("y").toFloat(), command.getString("text"))
        else if (type == "back") performGlobalAction(GLOBAL_ACTION_BACK)
        else if (type == "home") performGlobalAction(GLOBAL_ACTION_HOME)
        else if (type == "recents") performGlobalAction(GLOBAL_ACTION_RECENTS)
        else if (type == "notifications") performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)
        else if (type == "quick_settings") performGlobalAction(GLOBAL_ACTION_QUICK_SETTINGS)
        else if (type == "power_dialog") performGlobalAction(GLOBAL_ACTION_POWER_DIALOG)
        else if (type == "lock_screen") performGlobalAction(GLOBAL_ACTION_LOCK_SCREEN)
        else if (type == "toggle_split_screen") performGlobalAction(GLOBAL_ACTION_TOGGLE_SPLIT_SCREEN)
        else if (type == "dismiss_shade") dismissShade()
        else if (type == "launch") launch(command.getString("package"))
        else if (type == "home_page") homePage(command.getString("direction"))
        else if (type == "move_overlay") moveOverlay(command.getInt("x"), command.getInt("y"))
        else if (type == "end") {
            endSession("command", false)
            true
        } else if (type == "end_return") {
            endSession("command", true)
            true
        } else if (type == "user_action") {
            RunSession.userInstruction = command.getString("instruction")
            RunSession.warning = ""
            RunSession.phase = RunSession.Phase.AwaitingUser
            operation.presentUserAction()
            true
        } else if (type == "risk") {
            RunSession.userInstruction = ""
            RunSession.warning = command.getString("message")
            RunSession.phase = RunSession.Phase.AwaitingRisk
            operation.presentWarning()
            true
        } else if (type == "refuse") {
            RunSession.userInstruction = ""
            RunSession.warning = command.getString("message")
            RunSession.phase = RunSession.Phase.Refused
            operation.presentWarning()
            true
        } else if (type == "ask") {
            RunSession.userInstruction = ""
            RunSession.warning = ""
            RunSession.question = command.getString("question")
            RunSession.answering = false
            RunSession.phase = RunSession.Phase.AwaitingAnswer
            operation.presentQuestion()
            true
        }
        else if (type == "wait") {
            Thread.sleep(command.getLong("duration_ms")); true
        } else return JSONObject().put("ok", false).put("error", "未知指令")
        return JSONObject().put("ok", ok).put("error", if (ok) null else "指令未执行")
    }

    private fun homePage(direction: String): Boolean {
        val width = resources.displayMetrics.widthPixels.toFloat()
        val y = resources.displayMetrics.heightPixels * 0.55f
        val startX = if (direction == "left") width * 0.84f else width * 0.16f
        val endX = if (direction == "left") width * 0.16f else width * 0.84f
        return gesture(startX, y, endX, y, 320L)
    }

    private fun gesture(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long): Boolean {
        val path = Path().apply {
            moveTo(x1, y1)
            lineTo(x2, y2)
        }
        val stroke = GestureDescription.StrokeDescription(path, 0, durationMs)
        return dispatchGesture(GestureDescription.Builder().addStroke(stroke).build(), null, null)
    }

    private fun zoom(x: Float, y: Float, direction: String, distance: Float): Boolean {
        val width = resources.displayMetrics.widthPixels.toFloat()
        val height = resources.displayMetrics.heightPixels.toFloat()
        val near = 60f
        val far = near + distance
        val opening = direction != "out"
        val startGap = if (opening) near else far
        val endGap = if (opening) far else near
        fun stroke(fromY: Float, toY: Float): GestureDescription.StrokeDescription {
            val path = Path().apply {
                moveTo(x.coerceIn(8f, width - 8f), fromY.coerceIn(8f, height - 8f))
                lineTo(x.coerceIn(8f, width - 8f), toY.coerceIn(8f, height - 8f))
            }
            return GestureDescription.StrokeDescription(path, 0, 400)
        }
        val gesture = GestureDescription.Builder()
            .addStroke(stroke(y - startGap, y - endGap))
            .addStroke(stroke(y + startGap, y + endGap))
            .build()
        return dispatchGesture(gesture, null, null)
    }

    private fun inputAt(x: Float, y: Float, value: String): Boolean {
        val clipboard = getSystemService(ClipboardManager::class.java)
        clipboard.setPrimaryClip(ClipData.newPlainText("input", value))
        val tapped = gesture(x, y, x + 1f, y + 1f, 50)
        main.postDelayed({
            if (pasteFocused()) return@postDelayed
            val offset = 64f * resources.displayMetrics.density
            val pasteY = if (y < resources.displayMetrics.heightPixels / 2f) y + offset else y - offset
            gesture(x, pasteY, x + 1f, pasteY + 1f, 40)
        }, 700)
        return tapped
    }

    private fun pasteFocused(): Boolean {
        val roots = windows.mapNotNull { it.root }.filter { it.packageName?.toString() != packageName }
        val targets = roots.ifEmpty { listOfNotNull(rootInActiveWindow) }
        var pasted = false
        for (root in targets) {
            if (!pasted) {
                var node = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
                var depth = 0
                while (node != null && depth++ < 5 && !pasted) {
                    node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
                    pasted = node.performAction(AccessibilityNodeInfo.ACTION_PASTE)
                    val parent = if (pasted) null else node.parent
                    node.recycle()
                    node = parent
                }
            }
            root.recycle()
        }
        return pasted
    }

    private fun dismissShade(): Boolean {
        if (Build.VERSION.SDK_INT < 31) return false
        return performGlobalAction(GLOBAL_ACTION_DISMISS_NOTIFICATION_SHADE)
    }

    private fun launch(packageName: String): Boolean {
        val intent = packageManager.getLaunchIntentForPackage(packageName) ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            startActivity(intent)
            true
        } catch (_: RuntimeException) {
            false
        }
    }

    companion object {
        const val ACTION_CONNECT = "com.example.screenagent.CONNECT"
        const val ACTION_DISCONNECT = "com.example.screenagent.DISCONNECT"
        const val EXTRA_SERVER = "server"

        @Volatile var instance: ScreenAgentService? = null
        @Volatile var status: String = "无障碍未开启"
    }
}
