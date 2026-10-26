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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
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

private val terminalStates = setOf("completed", "failed", "cancelled", "outcome_unknown", "interrupted")

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
fun WorkflowScreen(modifier: Modifier = Modifier) {
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
    var speechStatus by remember { mutableStateOf("") }
    val tts = remember { TextToSpeech(context) { ttsReady = it == TextToSpeech.SUCCESS } }
    DisposableEffect(tts) {
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) { scope.launch { speechStatus = "正在播报。" } }
            override fun onDone(utteranceId: String?) { scope.launch { speechStatus = "语音播报已完成。" } }
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) { scope.launch { speechStatus = "本次播报未完成，请查看文字。" } }
        })
        onDispose { tts.stop(); tts.shutdown() }
    }
    LaunchedEffect(ttsReady) {
        if (ttsReady) { ttsSupported = tts.setLanguage(Locale.SIMPLIFIED_CHINESE) >= 0; tts.setSpeechRate(0.85f) }
    }
    val state = task?.optString("state").orEmpty()
    val message = task?.optString("message").orEmpty()
    val active = task != null && state !in terminalStates
    fun changed() { requestKey = UUID.randomUUID().toString() }
    fun newTask() {
        editMode = false; launchedScan = ""
        scanInFlight = false; scanOwner = ""; scanNonce = ""
        dismissedTaskId = taskId
        task = null; taskId = ""; timeline = ""; result = "请填写新任务的信息。"; changed()
        prefs.edit().remove("task_id").putString("dismissed_task", dismissedTaskId).apply()
    }
    fun fillInstruction(text: String) {
        val parsed = parseTaskInstruction(text)
        instruction = text
        recipient = parsed.recipient
        subject = parsed.subject
        body = parsed.body
        parsed.url?.let { webpage = it; source = "manual" }
        changed()
        result = if (recipient.isBlank()) "需要补充收件人的完整邮箱；不会根据称呼猜测。" else "已填入明确的信息，请核对下面的收件人、主题和正文。"
    }
    fun accept(response: JSONObject) {
        if (dismissedTaskId.isNotBlank() && response.optJSONObject("task")?.optString("id") == dismissedTaskId) return
        response.optJSONObject("task")?.let { incoming ->
            if (task != null && task!!.optString("id") != incoming.optString("id") && incoming.optString("created_at") < task!!.optString("created_at")) return
            if (task != null && task!!.optString("id") == incoming.optString("id") && incoming.optString("updated_at") < task!!.optString("updated_at")) return
            if (task?.optString("id") != incoming.optString("id") || task?.optString("updated_at").orEmpty() <= incoming.optString("updated_at")) {
                task = incoming; taskId = incoming.getString("id")
                prefs.edit().putString("task_id", taskId).apply()
                if (prefs.getString("live_task", "") != incoming.toString()) prefs.edit().putString("live_task", incoming.toString()).apply()
                if (watchedTask != taskId) { watchedTask = taskId; TaskConnectionService.watch(context, taskId) }
            }
        }
        result = response.optString("message", result)
    }
    fun action(path: String, payload: JSONObject = JSONObject()) {
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
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        instruction = text
        action("/api/tasks", JSONObject().put("instruction", text).put("request_key", requestKey).put("assistance_mode", prefs.getString("assistance_mode", "silverguide")))
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

    LaunchedEffect(state, taskId, task?.optString("scan_nonce"), lifecycleState, scanInFlight) {
        if (!scanInFlight && state == "awaiting_qr" && lifecycleState.isAtLeast(Lifecycle.State.RESUMED)) {
            val marker = taskId + task!!.optString("scan_nonce")
            if (launchedScan != marker) {
                launchedScan = marker
                openScanner()
            }
        }
    }


    Column(modifier.verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("小银助手 · SilverGuide", style = MaterialTheme.typography.headlineMedium)
        Text("扫码打开网页，核对后分享给家人。", fontSize = 20.sp)
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
                    if (state in setOf("completed", "outcome_unknown") && !current.optBoolean("delivery_confirmed")) {
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
        if (!active) {
            OutlinedTextField(instruction, onValueChange = { instruction = it; changed() }, label = { Text("告诉小银想做什么") }, modifier = Modifier.fillMaxWidth(), minLines = 2)
            Button(onClick = { startGoal(instruction) }, enabled = !loading && instruction.isNotBlank(), modifier = Modifier.fillMaxWidth().heightIn(min = 60.dp)) { Text("让小银完成这个任务", fontSize = 21.sp) }
            LocalVoiceInput(enabled = !loading, onRecognized = { text ->
                if (listOf("主题", "正文", "内容").any { it in text } && listOf("扫码", "扫描", "二维码", "打开", "发给", "发送", "分享").none { it in text }) {
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
            TextButton(onClick = { manualMode = !manualMode }) { Text("按字段准备邮件", fontSize = 18.sp) }
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
    if (receiptDialog && task != null) AlertDialog(onDismissRequest = { receiptDialog = false }, title = { Text("确认已经收到？") },
        text = { Text("请仅在收件箱中找到这封邮件，并核对主题和正文后确认。此结果会记录为您人工核对。") },
        confirmButton = { TextButton(onClick = {
            receiptDialog = false
            action("/tasks/$taskId/received", JSONObject().put("draft_digest", task!!.getString("draft_digest")).put("confirmed", true))
        }) { Text("已核对收到") } }, dismissButton = { TextButton(onClick = { receiptDialog = false }) { Text("还没有确认") } })
}
