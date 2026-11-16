package com.example.demo

import android.Manifest
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import androidx.activity.result.contract.ActivityResultContracts
import android.content.Intent
import android.provider.Settings
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import java.util.UUID

private val terminalStates = setOf("completed", "partial", "no_route", "failed", "cancelled", "outcome_unknown", "interrupted")

internal fun apiHost(): String {
    val emulator = android.os.Build.FINGERPRINT.let { it.contains("generic") || it.contains("emulator") }
        || android.os.Build.HARDWARE.let { it.contains("ranchu") || it.contains("goldfish") }
        || android.os.Build.MODEL.let { it.contains("Emulator") || it.contains("SDK") }
    return if (emulator) "10.0.2.2" else "127.0.0.1"
}

internal suspend fun request(path: String, payload: JSONObject? = null, method: String = "GET"): JSONObject =
    withContext(Dispatchers.IO) {
        val connection = URL("http://${apiHost()}:8000$path").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 5000
            connection.readTimeout = if (path == "/speech/transcribe") 60000 else 20000
            if (payload != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val result = JSONObject(stream?.bufferedReader()?.use { it.readText() }.orEmpty())
            if (code !in 200..299 || !result.optBoolean("ok", true))
                throw IOException(result.optString("detail", result.optString("message", "请求失败")))
            result
        } finally { connection.disconnect() }
    }

@Composable
internal fun WorkflowScreen(
    modifier: Modifier = Modifier,
    sharedScan: SharedScan? = null,
    onDismissSharedScan: () -> Unit = {},
) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val lifecycleState by lifecycle.currentStateFlow.collectAsState()
    val scope = rememberCoroutineScope()
    val prefs = remember { context.getSharedPreferences("silverguide", 0) }
    var recipient by rememberSaveable { mutableStateOf(prefs.getString("recipient", "").orEmpty()) }
    var subject by rememberSaveable { mutableStateOf("网页分享") }
    var body by rememberSaveable { mutableStateOf("") }
    var webpage by rememberSaveable { mutableStateOf("") }
    var source by rememberSaveable { mutableStateOf("text") }
    var instruction by rememberSaveable { mutableStateOf("") }
    var requestKey by rememberSaveable { mutableStateOf(UUID.randomUUID().toString()) }
    var task by remember { mutableStateOf<JSONObject?>(null) }
    var taskId by rememberSaveable { mutableStateOf(prefs.getString("task_id", "").orEmpty()) }
    var dismissedTaskId by rememberSaveable { mutableStateOf(prefs.getString("dismissed_task", "").orEmpty()) }
    var result by rememberSaveable { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var watchedTask by remember { mutableStateOf("") }
    var launchedScan by rememberSaveable { mutableStateOf("") }
    var scanInFlight by rememberSaveable { mutableStateOf(false) }
    var scanOwner by rememberSaveable { mutableStateOf("") }
    var scanNonce by rememberSaveable { mutableStateOf("") }
    var manualMode by rememberSaveable { mutableStateOf(false) }
    var transitFormOpen by rememberSaveable { mutableStateOf(false) }
    var contactsOpen by rememberSaveable { mutableStateOf(false) }
    var contactAlias by rememberSaveable { mutableStateOf("") }
    var contactAddress by rememberSaveable { mutableStateOf("") }
    var contactsList by remember { mutableStateOf(org.json.JSONArray()) }
    var editMode by rememberSaveable { mutableStateOf(false) }
    var editRecipient by rememberSaveable { mutableStateOf("") }
    var editSubject by rememberSaveable { mutableStateOf("") }
    var editBody by rememberSaveable { mutableStateOf("") }
    var advanced by rememberSaveable { mutableStateOf(false) }
    var debugPrompt by rememberSaveable { mutableStateOf("打开手机的设置，打开后结束任务。") }
    var timeline by remember { mutableStateOf("") }
    var speakEnabled by rememberSaveable { mutableStateOf(prefs.getBoolean("speak_enabled", false)) }
    var ttsReady by remember { mutableStateOf(false) }
    var ttsSupported by remember { mutableStateOf(true) }
    var receiptDialog by remember { mutableStateOf(false) }
    var viewedTransitCompletion by rememberSaveable { mutableStateOf("") }
    var speechStatus by remember { mutableStateOf("") }
    var showDemoPicker by rememberSaveable { mutableStateOf(false) }
    var selectedDemoId by rememberSaveable { mutableStateOf("") }
    var selectedDemoPrompt by rememberSaveable { mutableStateOf("") }
    var demoMetadataSupported by rememberSaveable { mutableStateOf(false) }
    var demoRecoverySupported by rememberSaveable { mutableStateOf(false) }
    var demoRecoveryStatus by remember { mutableStateOf("正在检查演示功能…") }
    val pageScroll = rememberScrollState()
    val tts = remember { TextToSpeech(context) { ttsReady = it == TextToSpeech.SUCCESS } }
    DisposableEffect(tts) {
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) { scope.launch { speechStatus = "正在播报。" } }
            override fun onDone(utteranceId: String?) { scope.launch {
                if (utteranceId == "route:" + prefs.getString("route_speech_query", ""))
                    prefs.edit().remove("route_speech_query").remove("route_speech_task").putBoolean("route_speech_active", false).apply()
                speechStatus = "语音播报已完成。"
            } }
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) { scope.launch {
                if (utteranceId == "route:" + prefs.getString("route_speech_query", ""))
                    prefs.edit().remove("route_speech_query").remove("route_speech_task").putBoolean("route_speech_active", false).apply()
                speechStatus = "本次播报未完成，请查看文字。"
            } }
        })
        onDispose { prefs.edit().remove("route_speech_query").remove("route_speech_task").putBoolean("route_speech_active", false).apply(); tts.stop(); tts.shutdown() }
    }
    LaunchedEffect(ttsReady) {
        if (ttsReady) { ttsSupported = tts.setLanguage(Locale.SIMPLIFIED_CHINESE) >= 0; tts.setSpeechRate(0.85f) }
    }
    fun stopRouteSpeech() {
        if (prefs.getBoolean("route_speech_active", false)) tts.stop()
        prefs.edit().remove("route_speech_query").remove("route_speech_task").putBoolean("route_speech_active", false).apply()
    }
    fun speakRoute(text: String, queryId: String) {
        if (queryId.isBlank() || task?.optString("query_id") != queryId || text.isBlank()) return
        if (!ttsReady || !ttsSupported) { speechStatus = "当前语音播报不可用，请查看路线文字。"; return }
        prefs.edit().putString("route_speech_query", queryId).putString("route_speech_task", taskId).putBoolean("route_speech_active", true).apply()
        if (tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "route:$queryId") == TextToSpeech.ERROR) {
            stopRouteSpeech(); speechStatus = "本次播报未完成，请查看路线文字。"
        }
    }
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_STOP) stopRouteSpeech() }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer); stopRouteSpeech() }
    }
    LaunchedEffect(Unit) { stopRouteSpeech() }
    val state = task?.optString("state").orEmpty()
    val message = task?.optString("message").orEmpty()
    val active = task != null && state !in terminalStates
    fun changed() { requestKey = UUID.randomUUID().toString() }
    fun newTask() {
        stopRouteSpeech(); transitFormOpen = false
        dismissedTaskId = taskId.ifBlank { dismissedTaskId }
        prefs.edit().remove("task_id").putString("dismissed_task", dismissedTaskId)
            .remove("live_task").remove("live_events").remove("events_task")
            .remove("connection_error").remove("active_scan_task")
            .remove("external_scan_task").remove("external_scan_nonce").apply()
        TaskConnectionService.dismiss(context, dismissedTaskId)
        watchedTask = ""
        editMode = false; editRecipient = ""; editSubject = ""; editBody = ""
        manualMode = false; contactsOpen = false; advanced = false
        contactAlias = ""; contactAddress = ""
        receiptDialog = false; viewedTransitCompletion = ""; showDemoPicker = false; launchedScan = ""
        scanInFlight = false; scanOwner = ""; scanNonce = ""
        task = null; taskId = ""; timeline = ""; result = ""; changed()
        instruction = ""; webpage = ""; body = ""; subject = "网页分享"; source = "text"
        recipient = prefs.getString("recipient", "").orEmpty()
        selectedDemoId = ""; selectedDemoPrompt = ""
        tts.stop(); speechStatus = ""
        scope.launch { pageScroll.scrollTo(0) }
    }
    fun fillInstruction(text: String) {
        selectedDemoId = ""; selectedDemoPrompt = ""
        val parsed = parseTaskInstruction(text)
        instruction = text
        recipient = parsed.recipient
        subject = parsed.subject
        body = parsed.body
        parsed.url?.let { webpage = it; source = "manual" }
        changed()
        result = "已填入您明确说出的内容。点击按钮后，小银会确认任务，并提示需要补充的信息。"
    }
    fun accept(response: JSONObject) {
        if (dismissedTaskId.isNotBlank() && response.optJSONObject("task")?.optString("id") == dismissedTaskId) return
        response.optJSONObject("task")?.let { incoming ->
            if (task != null && task!!.optString("id") != incoming.optString("id") && incoming.optString("created_at") < task!!.optString("created_at")) return
            if (task != null && task!!.optString("id") == incoming.optString("id") && incoming.optString("updated_at") < task!!.optString("updated_at")) return
            if (task?.optString("id") != incoming.optString("id") || task?.optString("updated_at").orEmpty() <= incoming.optString("updated_at")) {
                val queryChanged = task?.optString("query_id").orEmpty() != incoming.optString("query_id")
                // Viewing a completed route reuses the query, but its old socket
                // has already closed. Reconnect for that new active operation.
                val becameActive = task?.optString("state") in terminalStates && incoming.optString("state") !in terminalStates
                if (queryChanged || incoming.optString("state") == "cancelled") stopRouteSpeech()
                task = incoming; taskId = incoming.getString("id")
                prefs.edit().putString("task_id", taskId).apply()
                if (prefs.getString("live_task", "") != incoming.toString()) prefs.edit().putString("live_task", incoming.toString()).apply()
                if (watchedTask != taskId || queryChanged || becameActive) { watchedTask = taskId; TaskConnectionService.watch(context, taskId) }
            }
        }
        result = response.optString("message", result)
    }
    fun action(path: String, payload: JSONObject = JSONObject()) {
        if (path in setOf("/tasks/$taskId/control", "/api/tasks/$taskId/control") && payload.optString("action") in setOf("pause", "resume", "cancel")) {
            result = ""
            TaskConnectionService.submitControl(context, taskId, payload.getString("action"))
            return
        }
        scope.launch {
            loading = true
            try { accept(request(path, payload, "POST")) }
            catch (e: Exception) { result = e.message ?: "连接失败，请检查电脑后端和 USB 连接" }
            finally { loading = false }
        }
    }
    DisposableEffect(prefs) {
        val listener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
            scope.launch {
                if (key == "live_task") prefs.getString("live_task", null)?.let {
                    accept(JSONObject().put("task", JSONObject(it)))
                }
                if (key == "live_events" && prefs.getString("events_task", "") == taskId) {
                    val events = org.json.JSONArray(prefs.getString("live_events", "[]"))
                    timeline = (maxOf(0, events.length() - 6) until events.length()).joinToString("\n") { "• " + events.getJSONObject(it).optString("message") }
                }
                if (key == "connection_error") prefs.getString("connection_error", null)?.let { result = it }
            }
        }
        prefs.registerOnSharedPreferenceChangeListener(listener)
        onDispose { prefs.unregisterOnSharedPreferenceChangeListener(listener) }
    }
    LaunchedEffect(lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            try { accept(request("/api/tasks/latest")) }
            catch (_: Exception) { result = "暂时连接不到电脑，请检查后端与 USB 连接。" }
            awaitCancellation()
        }
    }
    val notifications = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    fun startGoal(text: String) {
        stopRouteSpeech()
        val demo = DemoCases.find(selectedDemoId)?.takeIf { text == selectedDemoPrompt }
        if (demo?.recoveryFault != null && !demoRecoverySupported) {
            result = "恢复演示暂不可用，请检查电脑连接后重新打开演示案例。"
            return
        }
        if (demo == null) { selectedDemoId = ""; selectedDemoPrompt = "" }
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        instruction = text
        val payload = JSONObject().put("instruction", text).put("request_key", requestKey)
            .put("assistance_mode", prefs.getString("assistance_mode", "silverguide"))
        if (demo != null && demoMetadataSupported) {
            payload.put("demo_case_id", demo.id)
            demo.recoveryFault?.let { payload.put("demo_fault", it) }
        }
        action("/api/tasks", payload)
    }

    fun openDemoPicker() {
        showDemoPicker = true
        demoRecoverySupported = false; demoMetadataSupported = false
        demoRecoveryStatus = "正在检查演示功能…"
        scope.launch {
            try {
                val capabilities = request("/api/system/demo-capabilities")
                demoMetadataSupported = (capabilities.optJSONArray("case_ids")?.length() ?: 0) > 0
                demoRecoverySupported = capabilities.optBoolean("recovery_supported", false) && demoMetadataSupported
                demoRecoveryStatus = if (demoRecoverySupported) "恢复演示已就绪" else "当前电脑后端暂不支持恢复演示。"
            } catch (_: Exception) {
                demoRecoveryStatus = "恢复演示暂不可用，请检查电脑连接并更新后端。"
            }
        }
    }

    fun useDemo(case: DemoCase) {
        if (active || loading) return
        if (case.recoveryFault != null && !demoRecoverySupported) return
        if (task != null) newTask()
        manualMode = false; contactsOpen = false; editMode = false
        selectedDemoId = case.id
        selectedDemoPrompt = DemoCases.promptFor(case, UUID.randomUUID().toString().take(8).uppercase(Locale.US))
        instruction = selectedDemoPrompt
        changed()
        result = ""
        showDemoPicker = false
        scope.launch { pageScroll.animateScrollTo(0) }
    }
    val scanner = rememberLauncherForActivityResult(ScanContract()) { scan ->
        scanInFlight = false
        prefs.edit().remove("active_scan_task").apply()
        val raw = scan.contents
        if (scanOwner.isNotBlank() && (taskId != scanOwner || state != "awaiting_qr" || task?.optString("scan_nonce") != scanNonce))
            result = "本轮扫码已暂停、取消或过期，请按当前任务继续。"
        else if (raw == null) result = "本次没有识别到二维码，可以重新扫描或手动填写网址。"
        else {
            if (scanOwner.isNotBlank()) {
                action("/api/tasks/$scanOwner/qr", JSONObject().put("url", raw).put("scan_nonce", scanNonce))
            } else {
            webpage = raw; source = "qr"; changed()
            scope.launch {
                try {
                    webpage = request("/url/check", JSONObject().put("url", raw), "POST").getString("url")
                    result = "二维码已识别，准备邮件时会打开并核对这个网址。"
                } catch (e: Exception) { result = "二维码尚未通过检查：${e.message}" }
            }
            }
        }
    }

    fun openScanner() {
        if (scanInFlight) return
        scanOwner = if (state == "awaiting_qr") taskId else ""
        scanNonce = if (scanOwner.isNotBlank()) task!!.getString("scan_nonce") else ""
        scanInFlight = true
        prefs.edit().putString("active_scan_task", scanOwner).apply()
        scanner.launch(ScanOptions().setCaptureActivity(TaskCaptureActivity::class.java).setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            .setPrompt("将镜头对准网页二维码").setOrientationLocked(false).setBeepEnabled(false).setTimeout(45000))
    }

    LaunchedEffect(state, taskId, task?.optString("scan_nonce"), lifecycleState, scanInFlight, sharedScan) {
        if (!scanInFlight && sharedScan == null && state == "awaiting_qr" && lifecycleState.isAtLeast(Lifecycle.State.RESUMED)) {
            val marker = taskId + task!!.optString("scan_nonce")
            if (launchedScan != marker) {
                launchedScan = marker
                openScanner()
            }
        }
    }


    Column(modifier.verticalScroll(pageScroll).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("小银助手", style = MaterialTheme.typography.headlineMedium)
                Text("SilverGuide", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            TextButton(onClick = { openDemoPicker() }) { Text("演示案例", fontSize = 17.sp) }
        }
        Text("扫码分享网页，也能查询公交地铁路线。", fontSize = 20.sp)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("语音播报任务进度", fontSize = 18.sp)
            Switch(speakEnabled, onCheckedChange = { speakEnabled = it; prefs.edit().putBoolean("speak_enabled", it).apply(); if (!it) tts.stop() })
        }
        if (speakEnabled && (!ttsReady || !ttsSupported)) Text("当前语音播报不可用，可继续查看文字进度。")
        if (speakEnabled && ttsReady && ttsSupported) {
            TextButton(onClick = {
                if (tts.speak("您好，我是小银助手。请核对邮件内容，确认后再发送。", TextToSpeech.QUEUE_FLUSH, null, "voice-preview") == TextToSpeech.ERROR)
                    speechStatus = "本次播报未完成，请查看文字。"
            }) { Text("试听语音播报", fontSize = 19.sp) }
            if (speechStatus.isNotBlank()) Text(speechStatus, fontSize = 18.sp)
        }
        if (result.isNotBlank() && result != message) Text(result, fontSize = 18.sp, color = MaterialTheme.colorScheme.primary)
        if (task != null && state in terminalStates)
            Button(onClick = { newTask() }, enabled = !loading, modifier = Modifier.fillMaxWidth().heightIn(min = 60.dp)) { Text("准备新的任务", fontSize = 21.sp) }
        task?.let { current ->
            if (current.optString("task_type") == "plan_transit") {
                key(taskId, current.optString("query_id")) {
                    TransitTaskCard(current, loading, timeline,
                        onAction = { path, payload -> action(path, payload) },
                        onSpeak = { text, query -> speakRoute(text, query) }, onStop = { stopRouteSpeech() })
                }
            } else {
            val draft = current.getJSONObject("draft")
            val fingerprint = current.getString("draft_digest")
            var checked by remember(taskId, fingerprint, state) { mutableStateOf(false) }
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("当前任务", style = MaterialTheme.typography.titleLarge)
                    Text(message, fontSize = 21.sp)
                    if (state == "needs_clarification") {
                        var answerAddress by remember(taskId) { mutableStateOf("") }
                        var answerUrl by remember(taskId) { mutableStateOf("") }
                        var saveContact by remember(taskId) { mutableStateOf(false) }
                        val fields = current.optJSONArray("clarification_fields")?.toString().orEmpty()
                        if ("recipient" in fields) {
                            OutlinedTextField(answerAddress, onValueChange = { answerAddress = it }, label = { Text("收件人的完整邮箱") }, modifier = Modifier.fillMaxWidth())
                            if (current.optString("recipient_alias").isNotBlank()) Row {
                                Checkbox(saveContact, onCheckedChange = { saveContact = it })
                                Text("记住“${current.optString("recipient_alias") }”对应的邮箱", fontSize = 18.sp)
                            }
                        }
                        if ("url" in fields) OutlinedTextField(answerUrl, onValueChange = { answerUrl = it }, label = { Text("需要打开或分享的网址") }, modifier = Modifier.fillMaxWidth())
                        Button(onClick = {
                            val reply = JSONObject().put("recipient", answerAddress).put("alias", current.optString("recipient_alias")).put("save_contact", saveContact)
                            if (answerUrl.isNotBlank()) reply.put("url", answerUrl)
                            action("/api/tasks/$taskId/clarify", reply)
                        }, enabled = !loading) { Text("补充好了，继续任务", fontSize = 20.sp) }
                    }
                    if (state == "awaiting_qr") Button(onClick = {
                        openScanner()
                    }) { Text("再次打开扫码", fontSize = 20.sp) }
                    if (current.optString("verification_detail").isNotBlank()) Text(current.getString("verification_detail"), fontSize = 18.sp)
                    if (current.optString("task_type") == "open_url") {
                        Text("识别网址 → 打开网页 → 核对地址", fontSize = 16.sp)
                        if (draft.optString("url").isNotBlank()) Text("网址：${draft.getString("url")}", fontSize = 20.sp)
                    } else {
                    Text("网址核对 → 准备草稿 → 您确认 → 发送 → 检查结果", fontSize = 16.sp)
                    Text("收件人：${draft.getString("recipient")}", fontSize = 20.sp)
                    Text("主题：${draft.getString("subject")}", fontSize = 20.sp)
                    Text("完整正文：\n${draft.getString("body")}", fontSize = 19.sp)
                    Text("正文末尾的任务标记用于核对本次邮件的发送记录。", fontSize = 16.sp)
                    }
                    if (state == "awaiting_confirmation") {
                        Text("草稿已从手机回读核对。确认后会再次检查内容，只发送一次。")
                        Row {
                            Checkbox(checked, onCheckedChange = { checked = it }, enabled = !loading)
                            Text("我已核对收件人、主题和完整正文", fontSize = 19.sp)
                        }
                        Button(onClick = { action("/tasks/$taskId/approve", JSONObject().put("draft_digest", fingerprint).put("confirmed", true).put("approval_nonce", current.getString("approval_nonce"))) },
                            enabled = checked && !editMode && !loading, modifier = Modifier.fillMaxWidth().heightIn(min = 60.dp)) { Text("确认发送这封邮件", fontSize = 21.sp) }
                        TextButton(onClick = {
                            editRecipient = draft.getString("recipient"); editSubject = draft.getString("subject")
                            editBody = draft.getString("body").removeSuffix("\n\n" + current.optString("verification_marker")); editMode = !editMode
                        }) { Text("修改草稿内容", fontSize = 19.sp) }
                        if (editMode) {
                            OutlinedTextField(editRecipient, onValueChange = { editRecipient = it }, label = { Text("修改收件邮箱") }, modifier = Modifier.fillMaxWidth())
                            OutlinedTextField(editSubject, onValueChange = { editSubject = it }, label = { Text("修改主题") }, modifier = Modifier.fillMaxWidth())
                            OutlinedTextField(editBody, onValueChange = { editBody = it }, label = { Text("修改正文") }, modifier = Modifier.fillMaxWidth(), minLines = 3)
                            Button(onClick = {
                                action("/api/tasks/$taskId/draft", JSONObject().put("recipient", editRecipient).put("subject", editSubject).put("body", editBody))
                                editMode = false
                            }, enabled = !loading) { Text("保存修改并重新核对", fontSize = 20.sp) }
                        }
                    }
                    if (active && state != "needs_review") {
                        OutlinedButton(onClick = { action("/tasks/$taskId/control", JSONObject().put("action", if (state == "paused") "resume" else "pause")) },
                            enabled = !loading, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) { Text(if (state == "paused") "继续任务" else "暂停任务", fontSize = 20.sp) }
                        OutlinedButton(onClick = { action("/tasks/$taskId/control", JSONObject().put("action", "cancel")) },
                            enabled = !loading, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) { Text("取消任务", fontSize = 20.sp) }
                    }
                    if (state in setOf("failed", "needs_review", "interrupted")) {
                        Button(onClick = { action("/tasks/$taskId/control", JSONObject().put("action", "retry_prepare")) }, enabled = !loading) { Text("重新准备并核对草稿", fontSize = 20.sp) }
                    }
                    if (state == "outcome_unknown") {
                        Text("请先核对收件箱或已发送记录，避免重复邮件。", fontSize = 19.sp)
                        Button(onClick = { action("/tasks/$taskId/control", JSONObject().put("action", "verify_only")) }, enabled = !loading) { Text("仅重新检查发送结果", fontSize = 20.sp) }
                    }
                    if (current.optString("task_type") != "open_url" && current.optInt("send_attempts") == 1 &&
                        state in setOf("completed", "outcome_unknown") && !current.optBoolean("delivery_confirmed")) {
                        OutlinedButton(onClick = { receiptDialog = true }, enabled = !loading) { Text("我已在收件箱核对收到", fontSize = 19.sp) }
                    }
                    if (state == "needs_review") {
                        TextButton(onClick = {
                            action("/tasks/$taskId/control", JSONObject().put("action", "cancel"))
                        }, enabled = !loading) { Text("取消并修改内容", fontSize = 19.sp) }
                    }
                    if (timeline.isNotEmpty()) Text(timeline, fontSize = 16.sp)
                    if (current.optString("assistance_mode") != "basic") TextButton(onClick = { scope.launch {
                        try { result = request("/api/tasks/$taskId/explain").getString("message") }
                        catch (e: Exception) { result = e.message.orEmpty() }
                    } }) { Text("解释当前步骤", fontSize = 19.sp) }
                }
            }
            }
        }
        if (task == null) {
            OutlinedButton(onClick = { transitFormOpen = !transitFormOpen }, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) {
                Text(if (transitFormOpen) "收起路线填写" else "公交 / 地铁路线", fontSize = 20.sp)
            }
            if (transitFormOpen) TransitRequestCard(enabled = !loading, onStart = { text ->
                selectedDemoId = ""; selectedDemoPrompt = ""; changed(); startGoal(text)
            })
            DemoCases.find(selectedDemoId)?.let { demo ->
                SelectedDemoCaseCard(case = demo, onClear = {
                    selectedDemoId = ""; selectedDemoPrompt = ""; instruction = ""; changed()
                }, canClear = !loading)
            }
            OutlinedTextField(instruction, onValueChange = {
                instruction = it
                if (it != selectedDemoPrompt) { selectedDemoId = ""; selectedDemoPrompt = "" }
                changed()
            }, label = { Text("告诉小银想做什么") }, modifier = Modifier.fillMaxWidth(), minLines = 2)
            Button(onClick = { startGoal(instruction) }, enabled = !loading && instruction.isNotBlank(), modifier = Modifier.fillMaxWidth().heightIn(min = 60.dp)) {
                Text(DemoCases.find(selectedDemoId)?.startLabel ?: "让小银完成这个任务", fontSize = 21.sp)
            }
            LocalVoiceInput(enabled = !loading, onRecognized = { text ->
                if (listOf("主题", "正文", "内容").any { it in text } && listOf("扫码", "扫描", "二维码", "打开", "发给", "发送", "分享", "公交", "地铁", "路线", "出发", "换乘").none { it in text }) {
                    fillInstruction(text); manualMode = true
                } else { changed(); startGoal(text) }
            }, onMessage = { result = it })
            TextButton(onClick = { contactsOpen = !contactsOpen; scope.launch {
                try { contactsList = request("/api/contacts").getJSONArray("contacts") } catch (e: Exception) { result = e.message.orEmpty() }
            } }) { Text("管理联系人称呼", fontSize = 19.sp) }
            if (contactsOpen) {
                for (i in 0 until contactsList.length()) {
                    val contact = contactsList.getJSONObject(i)
                    Text("${contact.getString("alias")}：${contact.getString("address")}", fontSize = 18.sp)
                    TextButton(onClick = { scope.launch {
                        try {
                            request("/api/contacts/" + java.net.URLEncoder.encode(contact.getString("alias"), "UTF-8").replace("+", "%20"), method = "DELETE")
                            contactsList = request("/api/contacts").getJSONArray("contacts")
                        } catch (e: Exception) { result = e.message.orEmpty() }
                    } }) { Text("删除这个称呼") }
                }
                OutlinedTextField(contactAlias, onValueChange = { contactAlias = it }, label = { Text("称呼，例如测试收件人") }, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(contactAddress, onValueChange = { contactAddress = it }, label = { Text("对应的完整邮箱") }, modifier = Modifier.fillMaxWidth())
                Button(onClick = { scope.launch {
                    try {
                        result = request("/api/contacts", JSONObject().put("alias", contactAlias).put("address", contactAddress), "PUT").getString("message")
                        contactsList = request("/api/contacts").getJSONArray("contacts")
                    } catch (e: Exception) { result = e.message.orEmpty() }
                } }, enabled = contactAlias.isNotBlank() && contactAddress.isNotBlank()) { Text("保存联系人称呼", fontSize = 20.sp) }
            }
            TextButton(onClick = {
                manualMode = !manualMode
                if (manualMode && selectedDemoId.isNotBlank()) {
                    selectedDemoId = ""; selectedDemoPrompt = ""; instruction = ""; changed()
                }
            }) { Text("按字段准备邮件", fontSize = 18.sp) }
            if (manualMode) {
            Button(onClick = { openScanner() },
                modifier = Modifier.fillMaxWidth().heightIn(min = 60.dp)) { Text("扫描网页二维码", fontSize = 21.sp) }
            OutlinedTextField(webpage, onValueChange = { webpage = it; source = "manual"; changed() }, label = { Text("网页网址（也可手动填写）") }, modifier = Modifier.fillMaxWidth())
            Text(if (source == "qr") "网址来源：二维码扫描" else "可扫描二维码，也可直接准备一封文字邮件。")
            OutlinedTextField(recipient, onValueChange = { recipient = it; changed() }, label = { Text("收件邮箱") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
            OutlinedTextField(subject, onValueChange = { subject = it; changed() }, label = { Text("邮件主题") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
            OutlinedTextField(body, onValueChange = { body = it; changed() }, label = { Text("正文（网址会自动加入正文）") }, modifier = Modifier.fillMaxWidth(), minLines = 3)
            Button(onClick = {
                prefs.edit().putString("recipient", recipient).apply()
                action("/tasks", JSONObject().put("recipient", recipient).put("subject", subject).put("body", body)
                    .put("url", webpage).put("source", source).put("request_key", requestKey))
            }, enabled = !loading && recipient.isNotBlank() && subject.isNotBlank() && (body.isNotBlank() || webpage.isNotBlank()),
                modifier = Modifier.fillMaxWidth().heightIn(min = 64.dp)) { Text(if (loading) "正在提交…" else "准备草稿，稍后由我确认发送", fontSize = 20.sp) }
            }

        }
        TextButton(onClick = { advanced = !advanced }) { Text("连接与基础控制", fontSize = 18.sp) }
        if (advanced) {
            var basicMode by remember { mutableStateOf(prefs.getString("assistance_mode", "silverguide") == "basic") }
            Row { Switch(basicMode, onCheckedChange = { basicMode = it; prefs.edit().putString("assistance_mode", if (it) "basic" else "silverguide").apply() }); Text("基础反馈对照模式（实验）", fontSize = 17.sp) }
            OutlinedButton(onClick = { context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }) { Text("打开无障碍设置", fontSize = 19.sp) }
            OutlinedButton(onClick = { action("/device/check") }, enabled = !loading) { Text("检查手机连接", fontSize = 19.sp) }
            OutlinedTextField(debugPrompt, onValueChange = { debugPrompt = it }, label = { Text("基础 AI 操作测试") }, modifier = Modifier.fillMaxWidth())
            OutlinedButton(onClick = { action("/recognize/start", JSONObject().put("prompt", debugPrompt)) }, enabled = !loading && !active) { Text("执行基础 AI 测试") }
            OutlinedButton(onClick = { scope.launch { try { result = request("/recognize/status").optString("message") } catch (e: Exception) { result = e.message.orEmpty() } } }) { Text("查看 AI 任务状态") }
            OutlinedButton(onClick = { action("/recognize/stop") }) { Text("停止 AI 执行") }
        }
        Spacer(Modifier.height(24.dp))
    }
    if (showDemoPicker) DemoCasePickerSheet(
        onDismiss = { showDemoPicker = false }, onSelect = { useDemo(it) },
        canSelect = !active && !loading, recoverySupported = demoRecoverySupported,
        recoveryStatus = demoRecoveryStatus,
    )
    sharedScan?.let { share ->
        // The share may cold-start the Activity before task restoration. Bind
        // once only after a successful fresh backend snapshot, and persist that
        // binding through rotation so a new QR round cannot inherit an old link.
        var bindingKind by rememberSaveable(share.id) { mutableStateOf("pending") }
        var boundTask by rememberSaveable(share.id) { mutableStateOf("") }
        var boundNonce by rememberSaveable(share.id) { mutableStateOf("") }
        val initialTaskId = rememberSaveable(share.id) { taskId.ifBlank { prefs.getString("external_scan_task", "").orEmpty() } }
        val initialNonce = rememberSaveable(share.id) {
            preparedScanNonce(initialTaskId, prefs.getString("external_scan_task", "").orEmpty(),
                prefs.getString("external_scan_nonce", "").orEmpty())
        }
        fun dismissShare() {
            prefs.edit().remove("external_scan_task").remove("external_scan_nonce").apply()
            onDismissSharedScan()
        }
        var lookupFailed by remember(share.id) { mutableStateOf(false) }
        var retryLookup by remember(share.id) { mutableIntStateOf(0) }
        LaunchedEffect(share.id, retryLookup) {
            if (bindingKind == "pending") {
                lookupFailed = false
                try {
                    val response = request("/api/tasks/latest")
                    val snapshot = response.optJSONObject("task")
                    val snapshotId = snapshot?.optString("id").orEmpty()
                    val resolved = if (snapshotId.isNotBlank() && snapshotId == dismissedTaskId)
                        ShareBinding(if (initialTaskId.isBlank() && taskId.isBlank() && snapshot?.optString("state").orEmpty() in terminalStates) "form" else "blocked")
                    else bindSharedScan(true, snapshotId, snapshot?.optString("state").orEmpty(),
                        snapshot?.optString("scan_nonce").orEmpty(), initialTaskId, initialNonce)
                    if (resolved.kind != "form") accept(response)
                    boundTask = resolved.taskId; boundNonce = resolved.nonce; bindingKind = resolved.kind
                } catch (_: Exception) { lookupFailed = true }
            }
        }
        val currentNonce = task?.optString("scan_nonce").orEmpty()
        val binding = ShareBinding(bindingKind, boundTask, boundNonce)
        val canContinue = shareBindingCurrent(binding, taskId, state, currentNonce) && !loading && !scanInFlight
        val canFill = bindingKind == "form" && taskId.isBlank() && !loading && !scanInFlight
        AlertDialog(
            onDismissRequest = { dismissShare() },
            title = { Text("核对分享过来的网址", fontSize = 24.sp) },
            text = {
                Column(Modifier.heightIn(max = 280.dp).verticalScroll(rememberScrollState())) {
                    Text(share.url ?: "没有收到单独的 HTTPS 网页链接，请从扫码结果页分享网址。", fontSize = 19.sp)
                    if (share.url != null) Text(
                        when {
                            bindingKind == "pending" && lookupFailed -> "暂时无法核对当前任务，请检查连接后重试。"
                            bindingKind == "pending" -> "正在核对当前任务，请稍候。"
                            canContinue -> "确认后，小银会在当前任务中打开并核对网址。"
                            canFill -> "确认后只填入邮件表单，不会开始任务。"
                            else -> "当前扫码轮次无法确认或已经变化，请返回查看进度，再重新分享。"
                        }, fontSize = 17.sp,
                    )
                }
            },
            confirmButton = {
                val url = share.url
                if (bindingKind == "pending" && lookupFailed) TextButton(onClick = { retryLookup++ }) { Text("重试", fontSize = 19.sp) }
                else if (url != null && (canContinue || canFill)) TextButton(onClick = {
                    dismissShare()
                    if (canContinue) {
                        action("/api/tasks/$taskId/qr", JSONObject().put("url", url)
                            .put("scan_nonce", currentNonce).put("scan_source", "external_share"))
                    } else {
                        webpage = url; source = "manual"; manualMode = true; transitFormOpen = false; changed()
                        result = "已填入分享的网址，提交后还会核对网页。"
                    }
                }) { Text(if (canContinue) "确认网址，继续" else "填入表单", fontSize = 19.sp) }
            },
            dismissButton = { TextButton(onClick = { dismissShare() }) { Text("返回", fontSize = 19.sp) } },
        )
    }
    if (receiptDialog && task != null) AlertDialog(onDismissRequest = { receiptDialog = false }, title = { Text("确认已经收到？") },
        text = { Text("请仅在收件箱中找到这封邮件，并核对主题和正文后确认。此结果会记录为您人工核对。") },
        confirmButton = { TextButton(onClick = {
            receiptDialog = false
            action("/tasks/$taskId/received", JSONObject().put("draft_digest", task!!.getString("draft_digest")).put("confirmed", true))
        }) { Text("已核对收到") } }, dismissButton = { TextButton(onClick = { receiptDialog = false }) { Text("还没有确认") } })
    task?.takeIf { current ->
        val route = current.optJSONObject("transit_result")
        val query = current.optString("query_id")
        val id = current.optString("id")
        current.optString("task_type") == "plan_transit" && route != null && id.isNotBlank() && query.isNotBlank() &&
            transitCompletionReady(current.optString("state"), current.optBoolean("route_verified") && route.optBoolean("route_verified"),
                route.optString("classification"), route.optString("task_id") == id && route.optString("query_id") == query,
                current.optBoolean("dingtalk_requested"), current.optString("dingtalk_status")) &&
            viewedTransitCompletion != "$id:$query"
    }?.let { completed ->
        val route = completed.getJSONObject("transit_result")
        val label = listOf(route.optJSONObject("origin")?.optString("name").orEmpty(),
            route.optJSONObject("destination")?.optString("name").orEmpty()).filter { it.isNotBlank() }.joinToString(" → ")
        AlertDialog(
            onDismissRequest = { },
            title = { Text("任务已完成", fontSize = 26.sp) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("任务已完成，请点击确认。", fontSize = 21.sp)
                    if (label.isNotBlank()) Text(label, fontSize = 19.sp)
                    Text(if (completed.optBoolean("dingtalk_requested")) "路线已核对，并已发送到您的钉钉文件助手。"
                        else "高德路线已查询并核对。", fontSize = 19.sp)
                }
            },
            confirmButton = {
                Button(onClick = { newTask() }, enabled = !loading,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 60.dp)) {
                    Text("确认，返回首页", fontSize = 21.sp)
                }
            },
            dismissButton = { TextButton(onClick = { viewedTransitCompletion = "${completed.optString("id")}:${completed.optString("query_id")}" }) {
                Text("查看路线", fontSize = 19.sp)
            } },
        )
    }
    task?.takeIf { current ->
        current.optString("task_type") != "plan_transit" && state == "completed" && if (current.optString("task_type") == "open_url")
            current.optBoolean("webpage_verified") else current.optBoolean("delivery_confirmed")
    }?.let { completed ->
        AlertDialog(
            onDismissRequest = { },
            title = { Text("任务已完成", fontSize = 26.sp) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("任务已完成，请点击确认。", fontSize = 21.sp)
                    Text(if (completed.optString("task_type") == "open_url") "网页已经打开，网址已核对。"
                        else "您已在收件箱核对收到这封邮件。", fontSize = 19.sp)
                }
            },
            confirmButton = {
                Button(onClick = { newTask() }, enabled = !loading,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 60.dp)) {
                    Text("确认，返回首页", fontSize = 21.sp)
                }
            },
        )
    }
}
