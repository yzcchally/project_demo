package com.example.demo

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

@Composable
internal fun PhoneWorkflowScreen(
    modifier: Modifier,
    sharedScan: SharedScan?,
    onDismissShare: () -> Unit,
    onUseComputer: () -> Unit,
    historyRequest: Int? = null,
    onHistoryOpened: () -> Unit = {},
    revisionRequest: String? = null,
    onRevisionOpened: () -> Unit = {},
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val app = context.applicationContext
    val vault = remember(app) { PhoneCloudStore(app) }
    val journal = remember(app) { PhoneTaskJournal(app) }
    val runtime = remember(app) { PhoneLanguageModel.hasRuntime(app) }
    val phoneVoiceReady = remember(app) { PhoneOnDeviceSpeech.isAvailable(app) }
    var offline by rememberSaveable { mutableStateOf(false) }
    var offlineReady by remember { mutableStateOf(PhoneLanguageModel.hasModel(app)) }
    var config by remember { mutableStateOf<PhoneCloudConfig?>(null) }
    var readingConfig by remember { mutableStateOf(true) }
    var toolsOpen by remember { mutableStateOf(false) }
    var cloudOpen by remember { mutableStateOf(false) }
    var speechOpen by remember { mutableStateOf(false) }
    var historyOpen by remember { mutableStateOf(false) }
    var historyRecordId by remember { mutableStateOf<String?>(null) }
    var records by remember { mutableStateOf(emptyList<PhoneTaskRecord>()) }
    var entry by rememberSaveable { mutableStateOf("") }
    var message by rememberSaveable { mutableStateOf("") }
    var recordId by rememberSaveable { mutableStateOf("") }
    var intent by rememberSaveable { mutableStateOf<PhoneIntent?>(null) }
    var family by rememberSaveable { mutableStateOf<String?>(null) }
    var scanOwner by rememberSaveable { mutableStateOf<PhoneIntent?>(null) }
    var scanId by rememberSaveable { mutableStateOf<String?>(null) }
    var imageOwnerId by rememberSaveable { mutableStateOf<String?>(null) }
    var imageMail by rememberSaveable { mutableStateOf<PhoneIntent?>(null) }
    var scannedMailPage by rememberSaveable { mutableStateOf<String?>(null) }
    var reviewedMailPage by rememberSaveable { mutableStateOf<String?>(null) }
    var openedMailPage by rememberSaveable { mutableStateOf<String?>(null) }
    var paused by rememberSaveable { mutableStateOf(false) }
    var handedOff by rememberSaveable { mutableStateOf(false) }
    var externalConfirmed by rememberSaveable { mutableStateOf(false) }
    var revisionOpen by rememberSaveable { mutableStateOf(false) }
    var revisionText by rememberSaveable { mutableStateOf("") }
    var revisionProblem by rememberSaveable { mutableStateOf("") }
    var revisionRecord by remember { mutableStateOf<PhoneTaskRecord?>(null) }
    var taskJob by remember { mutableStateOf<Job?>(null) }
    var installJob by remember { mutableStateOf<Job?>(null) }
    var progress by remember { mutableStateOf(0) }
    val scroll = rememberScrollState()

    LaunchedEffect(vault) {
        try {
            config = vault.load()
        } catch (error: CancellationException) {
            throw error
        } catch (_: Exception) {
            message = "请在设置里重新连接在线服务。"
        } finally {
            readingConfig = false
        }
    }

    suspend fun begin(label: String): String = try {
        journal.begin(label)
    } catch (error: CancellationException) {
        throw error
    } catch (_: Exception) {
        ""
    }

    fun note(text: String, status: PhoneTaskOutcome, id: String = recordId) {
        if (id.isBlank()) return
        scope.launch {
            try {
                journal.note(id, text, status)
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                // A history storage issue cannot change the observed device action.
            }
        }
    }

    fun updateStage(plan: PhoneIntent) {
        intent = plan
        entry = ""
        message = ""
        scope.launch { scroll.scrollTo(0) }
    }

    fun acceptLink(url: String?, mail: PhoneIntent?) {
        if (url == null || !isPhonePublicHttps(url)) {
            message = "二维码须是单独的公开 HTTPS 链接。本次没有打开网页。"
            note("已阻止不安全的二维码内容，可以重新扫码。", PhoneTaskOutcome.Failed)
            return
        }
        if (mail != null && url != scannedMailPage) {
            scannedMailPage = url
            openedMailPage = null
            reviewedMailPage = null
        }
        val next = mail?.copy(url = url, needsScan = false) ?: PhoneIntent(PhoneIntentType.Web, url = url)
        updateStage(next)
        note("已获得网页链接，等待核对。", PhoneTaskOutcome.Waiting)
    }

    val scanner = rememberLauncherForActivityResult(ScanContract()) { result ->
        val mail = scanOwner
        val ownerId = scanId
        scanOwner = null
        scanId = null
        // A result from a short-lived camera belongs only to the action that opened it.
        if (ownerId != null && ownerId == recordId) {
            val url = result.contents?.trim()
            if (url == null) message = "本次扫码已结束，可以重新扫码。"
            else acceptLink(url, mail)
        }
    }

    val imagePicker = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        val owner = imageOwnerId
        val mail = imageMail
        imageOwnerId = null
        imageMail = null
        if (uri != null && owner != null && owner == recordId && taskJob == null && !paused) {
            taskJob = scope.launch {
                try {
                    val value = decodePhoneQrImage(app, uri)?.trim()
                    if (owner == recordId) {
                        if (value == null) {
                            message = "这张图片里没读到二维码，可以换一张。"
                            note(message, PhoneTaskOutcome.Failed)
                        } else acceptLink(value, mail)
                    }
                } finally {
                    taskJob = null
                }
            }
        }
    }

    fun pickQrImage(mail: PhoneIntent? = null) {
        imageOwnerId = recordId
        imageMail = mail
        try {
            imagePicker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
        } catch (_: Exception) {
            imageOwnerId = null
            imageMail = null
            message = "手机暂时打不开相册。"
        }
    }

    fun scan(mail: PhoneIntent? = null) {
        scanOwner = mail
        scanId = recordId
        try {
            scanner.launch(ScanOptions().setCaptureActivity(PhoneScanCaptureActivity::class.java)
                .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                .setPrompt("将镜头对准网页二维码").setOrientationLocked(false).setBeepEnabled(false))
            note("已请求打开扫码相机。", PhoneTaskOutcome.Waiting)
        } catch (_: Exception) {
            scanOwner = null
            scanId = null
            message = "手机暂时打不开扫码相机。"
        }
    }

    LaunchedEffect(sharedScan?.id, paused) {
        if (sharedScan != null && !paused) {
            val waitingMail = intent?.takeIf { it.type == PhoneIntentType.Mail && it.needsScan }
            if (waitingMail == null) recordId = begin("接收分享来的网页")
            acceptLink(sharedScan.url, waitingMail)
            onDismissShare()
        }
    }

    fun dispatch(plan: PhoneIntent) {
        when (plan.type) {
            PhoneIntentType.Family -> {
                intent = null
                family = plan.alias
                entry = ""
                note("已进入联系人核对。", PhoneTaskOutcome.Waiting)
            }
            PhoneIntentType.Scan -> {
                updateStage(plan)
                scan()
            }
            PhoneIntentType.Web -> {
                if (isPhonePublicHttps(plan.url)) updateStage(plan)
                else message = "请告诉我想打开的网页链接。"
            }
            PhoneIntentType.Transit, PhoneIntentType.WeChat -> updateStage(plan)
            PhoneIntentType.Mail -> {
                updateStage(plan)
                if (plan.needsScan) scan(plan)
            }
            PhoneIntentType.Unsupported -> {
                intent = null
                message = "您想扫码发邮件、查公交地铁、联系家人，还是转发到微信？"
                note("等待进一步明确要办的任务。", PhoneTaskOutcome.Waiting)
            }
        }
    }

    fun startShortcut(type: PhoneIntentType, label: String) {
        if (taskJob != null) return
        PhoneTaskOverlay.clear()
        handedOff = false
        externalConfirmed = false
        taskJob = scope.launch {
            try {
                recordId = begin(label)
                message = ""
                dispatch(PhoneIntent(type, needsScan = type == PhoneIntentType.Mail))
            } finally {
                taskJob = null
            }
        }
    }

    fun clear() {
        if (taskJob != null) {
            taskJob?.cancel()
            if (offline) PhoneLanguageModel.close()
            note("用户取消本次理解。", PhoneTaskOutcome.Cancelled)
        } else if (intent != null && !handedOff) {
            note("用户已取消本次任务。", PhoneTaskOutcome.Cancelled)
        }
        PhoneTaskOverlay.clear()
        recordId = ""
        intent = null
        scanOwner = null
        scanId = null
        imageOwnerId = null
        imageMail = null
        scannedMailPage = null
        openedMailPage = null
        reviewedMailPage = null
        paused = false
        handedOff = false
        externalConfirmed = false
        revisionOpen = false
        revisionText = ""
        revisionProblem = ""
        revisionRecord = null
        entry = ""
        message = ""
    }

    fun submit() {
        val text = entry.trim()
        if (text.isEmpty() || taskJob != null || paused) return
        val active = intent
        if (active != null && nextPhoneQuestion(active) != null) {
            val answered = applyPhoneAnswer(active, text)
            if (answered == null) {
                message = "这条信息还需要确认，请按上面的问题再说一次。"
            } else {
                updateStage(answered)
                note("已补充当前问题的信息。", PhoneTaskOutcome.Waiting)
            }
            return
        }
        val settings = config
        if (!offline && !PhoneCloudModel.validCredential(settings?.chatKey.orEmpty())) {
            cloudOpen = true
            return
        }
        if (offline && (!runtime || !offlineReady)) {
            toolsOpen = true
            return
        }
        if (text.length < 2) return
        PhoneTaskOverlay.clear()
        handedOff = false
        externalConfirmed = false
        recordId = ""
        intent = null
        message = ""
        taskJob = scope.launch {
            try {
                recordId = begin(text)
                val result = if (offline) PhoneLanguageModel.understand(app, text)
                    else PhoneCloudModel.understand(checkNotNull(settings), text)
                dispatch(parsePhoneIntent(result, text))
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                message = error.message ?: "暂时没能理解，请再说一次。"
                note(message, PhoneTaskOutcome.Failed)
            } finally {
                taskJob = null
            }
        }
    }

    fun showHistory(activeOnly: Boolean = false) {
        scope.launch {
            try {
                historyRecordId = recordId.takeIf { activeOnly && it.isNotEmpty() }
                records = journal.records()
                historyOpen = true
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                message = "暂时无法读取手机任务历史。"
            }
        }
    }

    fun openRevision() {
        val current = intent ?: return
        if (taskJob != null || recordId.isBlank()) return
        if (handedOff && current.type != PhoneIntentType.Transit) {
            message = "请在刚才打开的应用中核对这次操作。"
            return
        }
        revisionProblem = ""
        revisionRecord = null
        if (!paused) note("已暂停，可补充指令。", PhoneTaskOutcome.Waiting)
        paused = true
        revisionOpen = true
    }

    fun submitRevision() {
        if (taskJob != null) return
        val current = intent ?: return
        val correction = revisionText.trim()
        if (correction.isEmpty()) return
        val id = recordId
        taskJob = scope.launch {
            try {
                revisionProblem = ""
                val revised = directPhoneTaskRevision(current, correction) ?: run {
                    val prompt = phoneTaskRevisionPrompt(current, correction)
                    val settings = config
                    val result = if (offline && offlineReady) PhoneLanguageModel.understand(app, prompt)
                    else if (!offline && settings != null && PhoneCloudModel.validCredential(settings.chatKey)) {
                        PhoneCloudModel.understand(settings, prompt)
                    } else throw IllegalStateException("暂时无法理解补充说明，请再试一次。")
                    revisedPhoneIntent(current, parsePhoneIntent(result, correction))
                }
                if (revised == null || revised == current) {
                    revisionProblem = "请说清要改哪一项。原方案暂时保留。"
                    return@launch
                }
                if (recordId != id || intent != current) return@launch
                PhoneTaskOverlay.clear()
                handedOff = false
                externalConfirmed = false
                scanOwner = null
                scanId = null
                imageMail = null
                imageOwnerId = null
                if (revised.type != current.type || revised.url != current.url) {
                    scannedMailPage = null
                    openedMailPage = null
                    reviewedMailPage = null
                }
                paused = false
                revisionOpen = false
                revisionText = ""
                if (revised.type == PhoneIntentType.Family) dispatch(revised) else updateStage(revised)
                message = "补充已应用，请核对更新后的任务。"
                note("补充指令：${correction.take(320)}", PhoneTaskOutcome.Waiting, id)
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                revisionProblem = "暂时没能理解补充说明。原方案暂时保留。"
            } finally {
                taskJob = null
            }
        }
    }

    LaunchedEffect(historyRequest) {
        if (historyRequest != null) {
            showHistory(activeOnly = true)
            onHistoryOpened()
        }
    }

    LaunchedEffect(revisionRequest) {
        if (revisionRequest != null) {
            openRevision()
            onRevisionOpened()
        }
    }

    LaunchedEffect(revisionOpen, recordId) {
        if (revisionOpen) {
            val id = recordId
            revisionRecord = try {
                journal.records().firstOrNull { it.id == id }
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                null
            }
        }
    }

    fun install(uri: Uri?) {
        if (installJob != null) return
        progress = 0
        installJob = scope.launch {
            try {
                PhoneModelInstall.install(app, uri) { progress = it }
                offlineReady = true
                message = "手机离线模型已就绪。"
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                message = error.message?.takeIf { it.any { char -> char in '\u4e00'..'\u9fff' } }
                    ?: "模型安装没有完成，请重试或导入已经下载的文件。"
            } finally {
                installJob = null
            }
        }
    }

    val modelPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {
        if (it != null) install(it)
    }

    val plan = intent
    val checkingMailLink = plan?.type == PhoneIntentType.Mail && scannedMailPage == plan.url && reviewedMailPage != plan.url
    val question = if (checkingMailLink) null else plan?.let(::nextPhoneQuestion)
    Column(
        modifier.fillMaxSize().verticalScroll(scroll).padding(horizontal = 20.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text("小银助手", style = MaterialTheme.typography.headlineMedium)
            Spacer(Modifier.weight(1f))
            TextButton(onClick = { showHistory() }, enabled = taskJob == null) { Text("历史") }
            TextButton(onClick = { toolsOpen = true }, enabled = taskJob == null) { Text("设置") }
        }
        if (question != null && !paused) Text(question.prompt, fontSize = 21.sp)
        if ((plan == null || question != null) && !paused) {
            OutlinedTextField(value = entry, onValueChange = { entry = it.take(1500) },
                enabled = taskJob == null, label = { Text(if (question == null) "告诉小银想做什么" else "请回答小银") },
                modifier = Modifier.fillMaxWidth(), minLines = 2, maxLines = 4)
            PhoneAction(if (question == null) "开始任务" else "继续", !readingConfig && taskJob == null && entry.isNotBlank(), ::submit)
            if (phoneVoiceReady || config.speechCredential().isNotBlank()) {
                PhoneCloudVoiceInput(config,
                    enabled = taskJob == null && !toolsOpen && !cloudOpen && !speechOpen && !historyOpen && family == null,
                    onRecognized = { entry = it.take(1500) })
            }
        }
        if (message.isNotBlank()) {
            Text(message, fontSize = 18.sp, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        }
        if (plan == null && taskJob == null) {
            PhoneAction("扫码发邮件") { startShortcut(PhoneIntentType.Mail, "扫码发邮件") }
            PhoneAction("高德公交地铁") { startShortcut(PhoneIntentType.Transit, "高德公交地铁") }
            PhoneAction("联系家人") { startShortcut(PhoneIntentType.Family, "联系家人") }
            PhoneAction("微信转发") { startShortcut(PhoneIntentType.WeChat, "微信转发") }
        }
        if (taskJob != null) TextButton(onClick = ::clear) { Text("取消") }
        if (paused) {
            Text("任务已暂停。", fontSize = 21.sp)
            PhoneAction("继续任务") {
                paused = false
                note("用户继续任务。", if (handedOff) PhoneTaskOutcome.HandedOff else PhoneTaskOutcome.Waiting)
            }
            if (plan != null) OutlinedButton(onClick = ::openRevision) { Text("修改指令", fontSize = 18.sp) }
        }
        if (plan != null && question == null && !paused) {
            when (plan.type) {
                PhoneIntentType.Scan -> {
                    PhoneAction("重新扫码", taskJob == null) { scan() }
                    OutlinedButton(onClick = { pickQrImage() }, enabled = taskJob == null) { Text("从相册选二维码") }
                }
                PhoneIntentType.Web -> {
                    Text(plan.url, fontSize = 17.sp)
                    PhoneAction("打开网页并核对地址") {
                        val id = recordId
                        if (openPhoneWeb(context, plan.url)) {
                            note("已打开手机浏览器。", PhoneTaskOutcome.HandedOff, id)
                            PhoneTaskOverlay.track(PhoneIntentType.Web)
                            scope.launch {
                                val verified = verifyPhoneBrowserAddress(plan.url, revealAddress = true) == PhoneBrowserVerification.Verified
                                val result = if (verified) "已核对浏览器显示的完整网页地址。" else "浏览器已打开，请核对地址。"
                                if (recordId == id) message = result
                                note(result, if (verified) PhoneTaskOutcome.Completed else PhoneTaskOutcome.HandedOff, id)
                            }
                        } else message = "手机暂时打不开这个网页。"
                    }
                    OutlinedButton(onClick = { updateStage(PhoneIntent(PhoneIntentType.WeChat, shareText = plan.url)) }) {
                        Text("转发到微信")
                    }
                }
                PhoneIntentType.Transit -> {
                    Text("从 ${plan.origin} 去 ${plan.destination}（${plan.city}）", fontSize = 20.sp)
                    PhoneAction("打开高德选择路线") {
                        val launched = openPhoneTransit(context, plan)
                        if (launched) {
                            handedOff = true
                            PhoneTaskOverlay.track(PhoneIntentType.Transit)
                        }
                        message = if (!launched) "手机暂时打不开高德地图。" else if (plan.origin == "当前位置") {
                            "请在高德核对目的地，选择公交地铁和开始导航。"
                        } else "请在高德核对目的地，填写出发地 ${plan.origin}，再选择公交地铁。"
                        note(message, if (launched) PhoneTaskOutcome.HandedOff else PhoneTaskOutcome.Failed)
                    }
                    if (plan.origin != "当前位置") TextButton(onClick = {
                        context.getSystemService(ClipboardManager::class.java)
                            ?.setPrimaryClip(ClipData.newPlainText("出发地", plan.origin))
                        message = "已复制出发地，可在高德粘贴。"
                    }) { Text("复制出发地") }
                }
                PhoneIntentType.Mail -> {
                    if (plan.needsScan) {
                        PhoneAction("扫网页二维码", taskJob == null) { scan(plan) }
                        OutlinedButton(onClick = { pickQrImage(plan) }, enabled = taskJob == null) { Text("从相册选二维码") }
                    } else if (checkingMailLink) {
                        Text(plan.url, fontSize = 17.sp)
                        PhoneAction("打开网页并核对地址") {
                            val id = recordId
                            if (openPhoneWeb(context, plan.url)) {
                                openedMailPage = plan.url
                                PhoneTaskOverlay.track(PhoneIntentType.Web)
                                note("已打开浏览器核对二维码地址。", PhoneTaskOutcome.Waiting, id)
                                scope.launch {
                                    val checked = verifyPhoneBrowserAddress(plan.url, revealAddress = true) == PhoneBrowserVerification.Verified
                                    if (recordId == id && intent == plan) {
                                        if (checked) reviewedMailPage = plan.url
                                        message = if (checked) "已核对浏览器显示的完整网页地址。" else "请在浏览器核对完整地址。"
                                        note(message, PhoneTaskOutcome.Waiting, id)
                                    }
                                }
                            } else message = "手机暂时打不开这个网页。"
                        }
                        if (openedMailPage == plan.url) OutlinedButton(onClick = {
                            reviewedMailPage = plan.url
                            message = "已记录您核对的网页地址。"
                            note("用户已核对浏览器网页地址。", PhoneTaskOutcome.Waiting)
                        }) { Text("我已核对网页地址") }
                    } else {
                        Text("收件人  ${plan.recipient}", fontSize = 20.sp)
                        if (plan.subject.isNotBlank()) Text("主题  ${plan.subject}", fontSize = 18.sp)
                        if (plan.body.isNotBlank()) Text(plan.body, fontSize = 18.sp)
                        if (plan.url.isNotBlank()) Text(plan.url, fontSize = 17.sp)
                        if (handedOff) Text("本次草稿已交给邮件应用，请在那里查看和确认。", fontSize = 18.sp)
                        else PhoneAction("核对并打开邮件草稿") {
                            val id = recordId
                            val launched = openPhoneMail(context, plan)
                            if (launched) {
                                handedOff = true
                                PhoneTaskOverlay.track(PhoneIntentType.Mail)
                                note("已将待核对的草稿交给手机邮件应用。", PhoneTaskOutcome.HandedOff, id)
                                scope.launch {
                                    val verified = verifyPhoneMailDraft(plan) == PhoneMailDraftVerification.Verified
                                    val result = if (verified) "已核对收件人、标题和正文。请在邮件里发送。"
                                        else "草稿已打开，请在邮件里核对后再发送。"
                                    if (recordId == id && intent == plan && !externalConfirmed) {
                                        message = result
                                        note(result, PhoneTaskOutcome.HandedOff, id)
                                    }
                                }
                            } else message = "请检查收件地址和手机邮件应用。"
                        }
                        if (!handedOff) TextButton(onClick = { updateStage(plan.copy(recipient = "")) }) { Text("修改收件人") }
                    }
                }
                PhoneIntentType.WeChat -> {
                    Text(plan.shareText, fontSize = 19.sp)
                        if (handedOff) Text("本次内容已交给微信，请在那里核对。", fontSize = 18.sp)
                        else PhoneAction("核对并打开微信") {
                        val launched = openPhoneWeChat(context, plan.shareText)
                        if (launched) {
                            handedOff = true
                            PhoneTaskOverlay.track(PhoneIntentType.WeChat)
                        }
                        message = if (launched) "请在微信选择聊天并发送。" else "手机暂时无法把内容交给微信。"
                        note(message, if (launched) PhoneTaskOutcome.HandedOff else PhoneTaskOutcome.Waiting)
                    }
                    OutlinedButton(onClick = {
                        context.getSystemService(Context.CLIPBOARD_SERVICE).let { it as? ClipboardManager }
                            ?.setPrimaryClip(ClipData.newPlainText("小银待转发内容", plan.shareText))
                        message = "已复制内容，可在微信里粘贴。"
                    }) { Text("复制内容") }
                }
                PhoneIntentType.Family -> if (handedOff) Text("请在电话应用核对号码。", fontSize = 18.sp)
                PhoneIntentType.Unsupported -> Unit
            }
        }
        val needsUserAction = handedOff && !paused && plan?.type in setOf(
            PhoneIntentType.Mail, PhoneIntentType.WeChat, PhoneIntentType.Family,
        )
        if (needsUserAction) {
            Text(if (externalConfirmed) "已记录您确认完成。" else "请在已打开的应用中操作，完成后可回来记录结果。", fontSize = 17.sp)
            if (!externalConfirmed) TextButton(onClick = {
                externalConfirmed = true
                note("用户已在对应应用自行核对并确认完成。", PhoneTaskOutcome.UserConfirmed)
            }) { Text("我已核对完成", fontSize = 17.sp) }
        }
        if (plan != null && !paused && !handedOff && taskJob == null) TextButton(onClick = {
            paused = true
            note("用户暂停任务，保留目前的核对步骤。", PhoneTaskOutcome.Waiting)
        }) { Text("暂停任务") }
        if (plan != null) TextButton(onClick = ::clear, enabled = taskJob == null) {
            Text(if (paused) "取消任务" else "返回首页")
        }
    }
    if (revisionOpen) AlertDialog(
        onDismissRequest = { if (taskJob == null) revisionOpen = false },
        title = { Text("修改指令") },
        text = {
            Column(Modifier.heightIn(max = 310.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("任务已暂停。确认补充后可核对新方案。", fontSize = 17.sp)
                revisionRecord?.let {
                    Text("原始指令", style = MaterialTheme.typography.titleSmall)
                    Text(it.instruction.take(160), fontSize = 16.sp)
                    if (it.steps.isNotEmpty()) {
                        Text("最近进度", style = MaterialTheme.typography.titleSmall)
                        it.steps.takeLast(2).forEach { step -> Text(step, fontSize = 15.sp) }
                    }
                }
                OutlinedTextField(value = revisionText, onValueChange = { revisionText = it.take(320) },
                    modifier = Modifier.fillMaxWidth(), enabled = taskJob == null,
                    label = { Text("补充指令") }, minLines = 2, maxLines = 3)
                if (taskJob != null) Text("正在理解补充说明…")
                if (revisionProblem.isNotBlank()) Text(revisionProblem, modifier = Modifier.semantics {
                    liveRegion = LiveRegionMode.Polite
                })
            }
        },
        confirmButton = { TextButton(onClick = ::submitRevision, enabled = taskJob == null && revisionText.isNotBlank()) {
            Text("应用补充", fontSize = 17.sp)
        } },
        dismissButton = { TextButton(onClick = {
            revisionOpen = false
            revisionText = ""
        }, enabled = taskJob == null) { Text("先不改", fontSize = 17.sp) } },
    )
    if (toolsOpen) AlertDialog(
        onDismissRequest = { if (installJob == null) toolsOpen = false },
        title = { Text("设置") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                TextButton(onClick = { toolsOpen = false; speechOpen = true }) { Text("在线语音设置") }
                TextButton(onClick = { toolsOpen = false; cloudOpen = true }) { Text("在线模型设置") }
                TextButton(onClick = { offline = !offline; message = "" }) {
                    Text(if (offline) "切换到在线理解" else "切换到手机离线理解")
                }
                if (offline) Text(if (offlineReady) "手机离线模型已就绪" else "手机离线模型尚未安装")
                if (offline && runtime && !offlineReady && installJob == null) {
                    TextButton(onClick = { install(null) }) { Text("下载手机模型（${PhoneModelInstall.sizeLabel}）") }
                    TextButton(onClick = { modelPicker.launch(arrayOf("*/*")) }) { Text("导入手机模型") }
                }
                if (installJob != null) {
                    Text("正在安装模型  $progress%")
                    TextButton(onClick = { installJob?.cancel() }) { Text("取消安装") }
                }
                TextButton(onClick = { toolsOpen = false; onUseComputer() }, enabled = installJob == null) {
                    Text("电脑增强功能")
                }
            }
        },
        confirmButton = { TextButton(onClick = { toolsOpen = false }, enabled = installJob == null) { Text("关闭") } },
    )
    if (cloudOpen) PhoneCloudSettingsDialog(config, onDismiss = { cloudOpen = false }, onSaved = {
        config = it
        cloudOpen = false
        offline = false
        message = "手机已连接在线理解。"
    })
    if (speechOpen) PhoneSpeechSettingsDialog(config, onDismiss = { speechOpen = false }, onSaved = {
        config = it
        speechOpen = false
        message = "在线语音已启用。"
    })
    if (historyOpen) PhoneTaskJournalDialog(records, onDismiss = { historyOpen = false }, onReuseInstruction = {
        clear()
        entry = it.take(1500)
        historyOpen = false
        message = "原需求已填回，核对后可以重新开始。"
    }, initialRecordId = historyRecordId)
    family?.let { alias -> FamilyContactDialog(onDismiss = { family = null }, requestedAlias = alias, onDialLaunched = {
        handedOff = true
        intent = PhoneIntent(PhoneIntentType.Family)
        PhoneTaskOverlay.track(PhoneIntentType.Family)
        note("已打开拨号页，等待用户核对号码并拨出。", PhoneTaskOutcome.HandedOff)
    }) }
}

@Composable
private fun PhoneAction(label: String, enabled: Boolean = true, onClick: () -> Unit) {
    Button(onClick = onClick, enabled = enabled, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) {
        Text(label, fontSize = 20.sp)
    }
}
