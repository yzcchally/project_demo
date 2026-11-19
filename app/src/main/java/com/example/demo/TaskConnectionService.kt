package com.example.demo

import android.annotation.SuppressLint
import android.app.*
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.Handler
import android.os.Build
import android.os.IBinder
import android.os.Looper
import android.speech.tts.TextToSpeech
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.Locale
import java.util.concurrent.TimeUnit

@SuppressLint("MissingPermission")
class TaskConnectionService : Service() {
    private val prefs by lazy { getSharedPreferences("silverguide", 0) }
    private val client = OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS).pingInterval(15, TimeUnit.SECONDS).build()
    private val http = client.newBuilder().readTimeout(20, TimeUnit.SECONDS).build()
    private val handler = Handler(Looper.getMainLooper())
    private var socket: WebSocket? = null
    private var watched = ""
    private var active = true
    private var latest: JSONObject? = null
    private var lastSpoken = ""
    private var ttsReady = false
    private var reconnectScheduled = false
    private var reconnectRetry: Runnable? = null
    private var destroyed = false
    private val controls = TaskControlQueue()
    private var controlReady = false
    private var controlRetry: Runnable? = null
    private var overlaySteps = emptyList<String>()
    private lateinit var tts: TextToSpeech
    private val terminal = setOf("completed", "partial", "no_route", "failed", "cancelled", "outcome_unknown", "interrupted")

    private val speechListener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
        if (key == "route_speech_active" && prefs.getBoolean("route_speech_active", false)) handler.post { if (!destroyed) tts.stop() }
    }
    override fun onCreate() {
        super.onCreate()
        prefs.registerOnSharedPreferenceChangeListener(speechListener)
        if (Build.VERSION.SDK_INT >= 26)
            getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel(CHANNEL, "小银助手任务", NotificationManager.IMPORTANCE_LOW))
        tts = TextToSpeech(this) { code ->
            handler.post {
                if (!destroyed) {
                    ttsReady = code == TextToSpeech.SUCCESS
                    if (ttsReady) {
                        tts.setLanguage(Locale.SIMPLIFIED_CHINESE); tts.setSpeechRate(.85f)
                        latest?.let { speak(it.optString("id"), it.optString("message")) }
                    }
                }
            }
        }
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val id = intent?.getStringExtra("task_id") ?: prefs.getString("task_id", "").orEmpty()
        if (intent?.action == dismissAction(this)) {
            if (watched.isBlank() || watched == id) {
                active = false; watched = ""; latest = null
                TaskOverlayState.publish(this, null)
                controlReady = false; controls.clear(); cancelControlRetry()
                handler.removeCallbacksAndMessages(null)
                socket?.cancel(); socket = null
                client.dispatcher.cancelAll()
                tts.stop()
                stopForeground(STOP_FOREGROUND_REMOVE)
                getSystemService(NotificationManager::class.java).cancel(NOTICE)
                stopSelfResult(startId)
                return START_NOT_STICKY
            }
            return START_STICKY
        }
        if (id.isBlank() || dismissed(id)) {
            if (watched.isBlank() || watched == id) {
                TaskOverlayState.publish(this, null)
                stopForeground(STOP_FOREGROUND_REMOVE)
                getSystemService(NotificationManager::class.java).cancel(NOTICE)
                stopSelfResult(startId)
                return START_NOT_STICKY
            }
            return START_STICKY
        }
        if (watched != id || socket == null) {
            if (watched != id) {
                latest = null; lastSpoken = ""
                cancelControlRetry()
                controls.select(id, prefs.getString("pending_control:$id", null))
            }
            watched = id; active = true
            startForeground(NOTICE, notification("正在连接任务进度", null))
            connect(id)
        }
        intent?.getStringExtra("control")?.let { control(id, it) }
        return START_STICKY
    }
    private fun dismissed(id: String) = id.isNotBlank() && prefs.getString("dismissed_task", "") == id
    private fun current(id: String) = !destroyed && id.isNotBlank() && watched == id && !dismissed(id)
    private fun connect(id: String) {
        if (!current(id) || !active) return
        controlReady = false
        TaskOverlayState.publish(this, null)
        reconnectRetry?.let { handler.removeCallbacks(it) }; reconnectRetry = null
        cancelControlRetry()
        reconnectScheduled = false
        val previous = socket; socket = null; previous?.cancel()
        val cursor = prefs.getLong("cursor:$id", 0)
        socket = client.newWebSocket(Request.Builder().url("ws://${apiHost()}:8000/ws/tasks/$id?after=$cursor").build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                handler.post {
                    if (current(id) && socket === webSocket) {
                        prefs.edit().remove("connection_error").apply()
                    }
                }
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                handler.post {
                    if (current(id) && socket === webSocket) {
                        try {
                            val response = JSONObject(text)
                            // Replayed controls must wait for the current task snapshot.
                            val snapshot = response.optString("event_type") == "snapshot" && response.optJSONObject("task")?.optString("id") == id
                            if (snapshot) controlReady = true
                            update(response)
                            if (snapshot && active && controls.reconcileSnapshot(latest?.optString("state").orEmpty())) persistControl(id)
                            publishTaskOverlay()
                            dispatchControl(id)
                        } catch (_: Exception) { reconnect(id) }
                    }
                }
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                handler.post { if (current(id) && socket === webSocket) reconnect(id) }
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                handler.post { if (current(id) && socket === webSocket) reconnect(id) }
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
                handler.post { if (current(id) && socket === webSocket) reconnect(id) }
            }
        })
    }
    private fun reconnect(id: String) {
        if (!current(id) || !active || reconnectScheduled) return
        controlReady = false
        TaskOverlayState.publish(this, null)
        reconnectScheduled = true
        prefs.edit().putString("connection_error", "连接暂时中断，正在恢复原任务。请检查电脑和 USB 连接。").apply()
        getSystemService(NotificationManager::class.java).notify(NOTICE, notification("连接中断，正在恢复", latest))
        reconnectRetry = Runnable { reconnectRetry = null; if (current(id) && active) connect(id) }
            .also { handler.postDelayed(it, 2500) }
    }
    private fun update(response: JSONObject) {
        val task = response.optJSONObject("task") ?: return
        val id = task.getString("id")
        if (!current(id)) return
        val old = latest
        if (old != null && old.optString("id") == id && old.optString("updated_at") > task.optString("updated_at")) return
        latest = task
        val events = if (prefs.getString("events_task", "") == id) JSONArray(prefs.getString("live_events", "[]")) else JSONArray()
        response.optJSONArray("events")?.let { history -> for (i in 0 until history.length()) events.put(history.getJSONObject(i)) }
        response.optJSONObject("event")?.let { events.put(it) }
        val unique = LinkedHashMap<Int, JSONObject>()
        for (i in 0 until events.length()) unique[events.getJSONObject(i).optInt("seq")] = events.getJSONObject(i)
        val trimmed = JSONArray(); unique.values.toList().takeLast(20).forEach { trimmed.put(it) }
        val state = task.optString("state")
        val transitQuery = task.optString("query_id").takeIf { task.optString("task_type") == "plan_transit" }
        overlaySteps = if (TaskOverlayPhase.fromTaskState(state) == null) emptyList() else taskOverlaySteps(trimmed, state, transitQuery)
        prefs.edit().putString("live_task", task.toString()).putString("live_events", trimmed.toString()).putString("events_task", id)
            .putLong("cursor:$id", maxOf(prefs.getLong("cursor:$id", 0), response.optLong("cursor", 0))).remove("connection_error").apply()
        val message = task.optString("message")
        speak(id, message)
        active = state !in terminal
        getSystemService(NotificationManager::class.java).notify(NOTICE, notification(message, task))
        if (!active) {
            controlReady = false; controls.clear(); persistControl(id); cancelControlRetry()
            socket?.close(1000, "任务已结束"); socket = null
            stopForeground(STOP_FOREGROUND_DETACH)
            handler.postDelayed({ if (!active && current(id)) stopSelf() }, 10000)
        }
    }
    private fun control(id: String, action: String) {
        if (!current(id) || !controls.enqueue(action)) return
        // Remove the touchable overlay before any queued control can resume work.
        TaskOverlayState.publish(this, null)
        persistControl(id)
        dispatchControl(id)
    }

    private fun publishTaskOverlay() {
        val task = latest
        val phase = TaskOverlayPhase.fromTaskState(task?.optString("state").orEmpty())
        val id = task?.optString("id").orEmpty()
        if (!current(id) || !active || !controlReady || phase == null || controls.pending != null || controls.inFlight != null) {
            TaskOverlayState.publish(this, null)
            return
        }
        TaskOverlayState.publish(this, TaskOverlaySnapshot(id, phase, overlaySteps)) { action -> control(id, action) }
    }
    private fun persistControl(id: String) {
        val editor = prefs.edit()
        controls.pending?.let { editor.putString("pending_control:$id", it) }
            ?: editor.remove("pending_control:$id")
        editor.apply()
    }
    private fun cancelControlRetry() {
        controlRetry?.let { handler.removeCallbacks(it) }
        controlRetry = null
    }
    private fun dispatchControl(id: String) {
        if (!current(id) || !active || !controlReady || latest == null) return
        val attempt = controls.begin() ?: return
        cancelControlRetry()
        val body = JSONObject().put("action", attempt.action).toString().toRequestBody("application/json; charset=utf-8".toMediaType())
        http.newCall(Request.Builder().url("http://${apiHost()}:8000/api/tasks/$id/control").post(body).build()).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                handler.post {
                    finishControl(id, attempt, null, true, "控制结果尚未确认，正在恢复连接并继续提交。")
                }
            }
            override fun onResponse(call: Call, response: Response) {
                response.use {
                    val result = try { JSONObject(it.body?.string().orEmpty()) } catch (_: Exception) { null }
                    val acknowledged = it.isSuccessful && result?.optBoolean("ok", true) == true && result.optJSONObject("task")?.optString("id") == id
                    val retryable = if (it.isSuccessful) !acknowledged else it.code == 408 || it.code == 429 || it.code >= 500
                    val error = if (acknowledged) null else result?.optString("detail")?.takeIf { detail -> detail.isNotBlank() }
                        ?: if (retryable) "控制结果尚未确认，正在恢复连接并继续提交。" else "这次控制未被接受，请打开助手查看任务状态。"
                    handler.post { finishControl(id, attempt, result.takeIf { acknowledged }, retryable, error) }
                }
            }
        })
    }
    private fun finishControl(id: String, attempt: TaskControlQueue.Attempt, result: JSONObject?, retryable: Boolean, error: String?) {
        if (!current(id)) return
        val stillRequested = controls.pending == attempt.action
        if (!controls.complete(attempt, retryable)) return
        persistControl(id)
        result?.let { update(it) }
        publishTaskOverlay()
        if (error != null && stillRequested && active) prefs.edit().putString("connection_error", error).apply()
        if (controls.pending == null) return
        if (retryable && stillRequested) {
            // A timeout can follow a successful operation. Read a new snapshot
            // before deciding whether this control still needs to be sent.
            controlReady = false
            cancelControlRetry()
            controlRetry = Runnable { controlRetry = null; if (current(id) && active) connect(id) }
                .also { handler.postDelayed(it, 2500) }
        } else dispatchControl(id)
    }
    private fun speak(id: String, message: String) {
        if (current(id) && ttsReady && !prefs.getBoolean("route_speech_active", false) && prefs.getBoolean("speak_enabled", false) && message.isNotBlank() && message != lastSpoken) {
            lastSpoken = message; tts.speak(message, TextToSpeech.QUEUE_FLUSH, null, "task-progress")
        }
    }
    private fun notification(message: String, task: JSONObject?): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val builder = NotificationCompat.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("小银助手").setContentText(message).setStyle(NotificationCompat.BigTextStyle().bigText(message))
            .setContentIntent(open).setOnlyAlertOnce(true).setOngoing(task == null || task.optString("state") !in terminal)
        if (task != null && task.optString("state") !in terminal) {
            val actions = mutableListOf<Pair<String, String>>()
            if (task.optString("state") != "needs_review") actions.add(if (task.optString("state") == "paused") "继续" to "resume" else "暂停" to "pause")
            actions.add("取消" to "cancel")
            for ((index, pair) in actions.withIndex()) {
                val action = Intent(this, TaskConnectionService::class.java).putExtra("task_id", watched).putExtra("control", pair.second)
                builder.addAction(0, pair.first, PendingIntent.getService(this, index + 1, action, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
            }
        }
        return builder.build()
    }
    override fun onDestroy() {
        destroyed = true; active = false; ttsReady = false
        TaskOverlayState.publish(this, null)
        controlReady = false; controls.clear(); cancelControlRetry()
        prefs.unregisterOnSharedPreferenceChangeListener(speechListener)
        handler.removeCallbacksAndMessages(null); socket?.cancel(); client.dispatcher.cancelAll()
        tts.stop(); tts.shutdown(); super.onDestroy()
    }
    override fun onBind(intent: Intent?): IBinder? = null
    companion object {
        const val CHANNEL = "silverguide_tasks"
        const val NOTICE = 7001
        private fun dismissAction(context: Context) = "${context.packageName}.DISMISS_TASK"
        fun watch(context: Context, taskId: String) {
            if (taskId.isBlank() || context.getSharedPreferences("silverguide", 0).getString("dismissed_task", "") == taskId) return
            ContextCompat.startForegroundService(context, Intent(context, TaskConnectionService::class.java).putExtra("task_id", taskId))
        }
        fun submitControl(context: Context, taskId: String, action: String) {
            if (taskId.isBlank() || action !in setOf("pause", "resume", "cancel") ||
                context.getSharedPreferences("silverguide", 0).getString("dismissed_task", "") == taskId) return
            ContextCompat.startForegroundService(context, Intent(context, TaskConnectionService::class.java)
                .putExtra("task_id", taskId).putExtra("control", action))
        }
        fun dismiss(context: Context, taskId: String) {
            if (taskId.isBlank()) return
            context.getSharedPreferences("silverguide", 0).edit()
                .putString("dismissed_task", taskId).remove("pending_control:$taskId").apply()
            context.startService(Intent(context, TaskConnectionService::class.java)
                .setAction(dismissAction(context)).putExtra("task_id", taskId))
        }
    }
}
