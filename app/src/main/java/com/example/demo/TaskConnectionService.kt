package com.example.demo

import android.annotation.SuppressLint
import android.app.*
import android.content.Context
import android.content.Intent
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
    private lateinit var tts: TextToSpeech
    private val terminal = setOf("completed", "failed", "cancelled", "outcome_unknown", "interrupted")

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= 26)
            getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel(CHANNEL, "小银助手任务", NotificationManager.IMPORTANCE_LOW))
        tts = TextToSpeech(this) { code ->
            ttsReady = code == TextToSpeech.SUCCESS
            if (ttsReady) { tts.setLanguage(Locale.SIMPLIFIED_CHINESE); tts.setSpeechRate(.85f); latest?.let { speak(it.optString("message")) } }
        }
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val id = intent?.getStringExtra("task_id") ?: prefs.getString("task_id", "").orEmpty()
        if (id.isBlank()) { stopSelf(); return START_NOT_STICKY }
        if (watched != id || socket == null) {
            watched = id; active = true
            startForeground(NOTICE, notification("正在连接任务进度", null))
            connect(id)
        }
        intent?.getStringExtra("control")?.let { control(id, it) }
        return START_STICKY
    }
    private fun connect(id: String) {
        if (watched != id || !active) return
        reconnectScheduled = false
        val previous = socket; socket = null; previous?.cancel()
        val cursor = prefs.getLong("cursor:$id", 0)
        socket = client.newWebSocket(Request.Builder().url("ws://${apiHost()}:8000/ws/tasks/$id?after=$cursor").build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                prefs.edit().remove("connection_error").apply()
                prefs.getString("pending_control:$id", null)?.let { control(id, it) }
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (watched != id) return
                try { update(JSONObject(text)) } catch (_: Exception) { reconnect(id) }
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) { if (socket === webSocket) reconnect(id) }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { if (socket === webSocket) reconnect(id) }
        })
    }
    private fun reconnect(id: String) {
        if (watched != id || !active || reconnectScheduled) return
        reconnectScheduled = true
        prefs.edit().putString("connection_error", "连接暂时中断，正在恢复原任务。请检查电脑和 USB 连接。").apply()
        getSystemService(NotificationManager::class.java).notify(NOTICE, notification("连接中断，正在恢复", latest))
        handler.postDelayed({ if (watched == id && active) connect(id) }, 2500)
    }
    private fun update(response: JSONObject) {
        val task = response.optJSONObject("task") ?: return
        val id = task.getString("id")
        if (id != watched) return
        val old = latest
        if (old != null && old.optString("id") == id && old.optString("updated_at") > task.optString("updated_at")) return
        latest = task
        val events = if (prefs.getString("events_task", "") == id) JSONArray(prefs.getString("live_events", "[]")) else JSONArray()
        response.optJSONArray("events")?.let { history -> for (i in 0 until history.length()) events.put(history.getJSONObject(i)) }
        response.optJSONObject("event")?.let { events.put(it) }
        val unique = LinkedHashMap<Int, JSONObject>()
        for (i in 0 until events.length()) unique[events.getJSONObject(i).optInt("seq")] = events.getJSONObject(i)
        val trimmed = JSONArray(); unique.values.toList().takeLast(20).forEach { trimmed.put(it) }
        prefs.edit().putString("live_task", task.toString()).putString("live_events", trimmed.toString()).putString("events_task", id)
            .putLong("cursor:$id", response.optLong("cursor", prefs.getLong("cursor:$id", 0))).remove("connection_error").apply()
        val message = task.optString("message")
        handler.post { speak(message) }
        active = task.optString("state") !in terminal
        getSystemService(NotificationManager::class.java).notify(NOTICE, notification(message, task))
        if (!active) {
            socket?.close(1000, "任务已结束")
            handler.post { stopForeground(STOP_FOREGROUND_DETACH) }
            handler.postDelayed({ if (!active && watched == id) stopSelf() }, 10000)
        }
    }
    private fun control(id: String, action: String) {
        prefs.edit().putString("pending_control:$id", action).apply()
        val body = JSONObject().put("action", action).toString().toRequestBody("application/json; charset=utf-8".toMediaType())
        http.newCall(Request.Builder().url("http://${apiHost()}:8000/api/tasks/$id/control").post(body).build()).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                prefs.edit().putString("connection_error", "控制请求尚未送达，恢复连接后继续提交。").apply()
            }
            override fun onResponse(call: Call, response: Response) {
                response.use {
                    try {
                        val result = JSONObject(it.body?.string().orEmpty())
                        if (it.isSuccessful) { prefs.edit().remove("pending_control:$id").apply(); update(result) }
                        else prefs.edit().putString("connection_error", result.optString("detail", "请打开助手查看任务状态")).apply()
                    } catch (_: Exception) { }
                }
            }
        })
    }
    private fun speak(message: String) {
        if (ttsReady && prefs.getBoolean("speak_enabled", false) && message.isNotBlank() && message != lastSpoken) {
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
        active = false; handler.removeCallbacksAndMessages(null); socket?.cancel(); client.dispatcher.cancelAll()
        tts.stop(); tts.shutdown(); super.onDestroy()
    }
    override fun onBind(intent: Intent?): IBinder? = null
    companion object {
        const val CHANNEL = "silverguide_tasks"
        const val NOTICE = 7001
        fun watch(context: Context, taskId: String) {
            ContextCompat.startForegroundService(context, Intent(context, TaskConnectionService::class.java).putExtra("task_id", taskId))
        }
    }
}
