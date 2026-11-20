package com.example.screenagent

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private val PageBg = Color(0xFFF2F4F7)
private val Ink = Color(0xFF1D2939)
private val Muted = Color(0xFF667085)
private val Line = Color(0xFFE4E7EC)
private val Blue = Color(0xFF175CD3)
private val BlueSoft = Color(0xFFEFF4FF)
private val Amber = Color(0xFFB54708)
private val AmberSoft = Color(0xFFFFF6ED)
private val Teal = Color(0xFF027A48)
private val TealSoft = Color(0xFFE7F6EE)
private val Red = Color(0xFFB42318)
private val RedSoft = Color(0xFFFEF3F2)
private val Reply = Color(0xFF3E4784)
private val ReplySoft = Color(0xFFF4F3FF)
private val CardShape = RoundedCornerShape(16.dp)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(
                colorScheme = lightColorScheme(
                    primary = Blue,
                    onPrimary = Color.White,
                    background = PageBg,
                    surface = Color.White,
                ),
            ) {
                when (RunSession.phase) {
                    RunSession.Phase.Input -> InputScreen(
                        onStart = ::start,
                        onLoadRecords = ::loadRecords,
                        onOpenRecord = ::openRecord,
                        onDeleteRecord = ::deleteRecord,
                    )
                    RunSession.Phase.Review -> ReviewScreen(
                        onContinue = ::continueDialogue,
                        onNew = ::newDialogue,
                        onLoadRecords = ::loadRecords,
                        onOpenRecord = ::openRecord,
                        onDeleteRecord = ::deleteRecord,
                    )
                    RunSession.Phase.Revising -> ReviseScreen(
                        onApply = ::applyRevision,
                        onCancel = ::cancelRevision,
                        onEnd = ::end,
                    )
                    RunSession.Phase.AwaitingAnswer -> if (RunSession.answering) {
                        AnswerScreen(onSubmit = ::submitAnswer, onEnd = ::end)
                    } else {
                        OperatingScreen(onEnd = ::end)
                    }
                    else -> OperatingScreen(onEnd = ::end)
                }
            }
        }
    }

    private var starting = false

    private fun server(): String {
        return getSharedPreferences("screen-agent", MODE_PRIVATE)
            .getString("server", "http://127.0.0.1:8000")
            ?: "http://127.0.0.1:8000"
    }

    private fun start(task: String) {
        val text = task.trim()
        if (text.isEmpty()) {
            RunSession.notice = "请先输入要做的事"
            return
        }
        if (starting) return
        val blocked = RunSession.startBlockedReason(this)
        if (blocked != null) {
            RunSession.notice = blocked
            if (ScreenAgentService.instance == null) {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
            return
        }
        starting = true
        RunSession.notice = ""
        val address = server()
        Thread {
            try {
                val created = RecordApi.create(address)
                runOnUiThread {
                    starting = false
                    launchNew(text, address, created)
                }
            } catch (_: Exception) {
                runOnUiThread {
                    starting = false
                    RunSession.notice = "建不了记录，先看电脑上的后端开了没有"
                }
            }
        }.start()
    }

    private fun launchNew(text: String, address: String, created: RecordApi.Item) {
        RunSession.recordId = created.id
        RunSession.recordName = created.name
        RunSession.dialoguePrompt = text
        RunSession.dialogues = emptyList()
        RunSession.lastStep = 0
        RunSession.task = text
        RunSession.notice = ""
        RunSession.phase = RunSession.Phase.Operating
        val shown = ScreenAgentService.instance?.showOverlay() == true
        if (!shown) {
            RunSession.phase = RunSession.Phase.Input
            RunSession.notice = "无法显示操作条"
            return
        }
        ScreenAgentService.instance?.begin(text, address, created.id)
        goHome()
    }

    private fun continueDialogue(text: String) {
        val blocked = RunSession.startBlockedReason(this)
        if (blocked != null) {
            RunSession.notice = blocked
            return
        }
        val id = RunSession.recordId
        if (id.isBlank()) {
            RunSession.notice = "没有记录"
            return
        }
        val extra = text.trim()
        val address = server()
        RunSession.dialoguePrompt = extra
        RunSession.task = extra.ifBlank { RunSession.dialogues.firstOrNull()?.prompt.orEmpty() }
        RunSession.notice = ""
        RunSession.phase = RunSession.Phase.Operating
        val shown = ScreenAgentService.instance?.showOverlay() == true
        if (!shown) {
            RunSession.phase = RunSession.Phase.Review
            RunSession.notice = "无法显示操作条"
            return
        }
        ScreenAgentService.instance?.resume(extra, address, id, RunSession.lastStep)
        goHome()
    }

    private fun newDialogue() {
        RunSession.phase = RunSession.Phase.Input
        RunSession.task = ""
        RunSession.dialoguePrompt = ""
        RunSession.recordId = ""
        RunSession.recordName = "未命名"
        RunSession.dialogues = emptyList()
        RunSession.pendingReply = 0
        RunSession.progress = emptyList()
        RunSession.additions = emptyList()
        RunSession.notice = ""
    }

    private fun loadRecords(done: (List<RecordApi.Item>) -> Unit) {
        val address = server()
        Thread {
            val rows = try {
                RecordApi.list(address)
            } catch (_: Exception) {
                emptyList()
            }
            runOnUiThread { done(rows) }
        }.start()
    }

    private fun openRecord(id: String) {
        val address = server()
        Thread {
            try {
                val detail = RecordApi.detail(address, id)
                runOnUiThread {
                    RunSession.recordId = detail.id
                    RunSession.recordName = detail.name
                    RunSession.lastStep = detail.lastStep
                    RunSession.dialogues = detail.dialogues
                    RunSession.pendingReply = 0
                    RunSession.notice = ""
                    RunSession.phase = RunSession.Phase.Review
                }
            } catch (_: Exception) {
                runOnUiThread { RunSession.notice = "打不开这条记录" }
            }
        }.start()
    }

    private fun deleteRecord(id: String, done: () -> Unit) {
        val address = server()
        Thread {
            try {
                RecordApi.delete(address, id)
                runOnUiThread {
                    if (RunSession.recordId == id) newDialogue()
                    done()
                }
            } catch (_: Exception) {
                runOnUiThread { RunSession.notice = "删不掉这条记录" }
            }
        }.start()
    }

    private fun goHome() {
        startActivity(Intent(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_HOME)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        })
    }

    private fun submitAnswer(answer: String) {
        ScreenAgentService.instance?.submitAnswer(answer)
        moveTaskToBack(true)
    }

    private fun applyRevision(index: Int, addition: String) {
        ScreenAgentService.instance?.applyRevision(index, addition)
        moveTaskToBack(true)
    }

    private fun cancelRevision() {
        RunSession.phase = RunSession.Phase.Paused
        ScreenAgentService.instance?.showOverlay()
        ScreenAgentService.instance?.reportPhase()
        moveTaskToBack(true)
    }

    private fun end() {
        val service = ScreenAgentService.instance
        if (service != null) {
            service.endSession("button")
        } else {
            RunSession.phase = RunSession.Phase.Input
            RunSession.notice = ""
        }
    }
}

@Composable
private fun Page(content: @Composable ColumnScope.() -> Unit) {
    val scroll = rememberScrollState()
    var viewport by remember { mutableIntStateOf(0) }
    Box(
        Modifier
            .fillMaxSize()
            .background(PageBg)
            .imePadding()
            .onSizeChanged { viewport = it.height },
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .verticalScroll(scroll)
                .padding(start = 20.dp, top = 22.dp, end = 18.dp, bottom = 28.dp),
            content = content,
        )
        PageBar(scroll, viewport)
    }
}

@Composable
private fun BoxScope.PageBar(scroll: ScrollState, viewport: Int) {
    if (viewport <= 0 || scroll.maxValue <= 0) return
    val density = androidx.compose.ui.platform.LocalDensity.current
    val inset = with(density) { 10.dp.toPx() }
    val track = (viewport - inset * 2).coerceAtLeast(1f)
    val total = viewport + scroll.maxValue
    val thumb = (track * viewport / total.toFloat()).coerceIn(with(density) { 36.dp.toPx() }, track)
    val travel = (track - thumb).coerceAtLeast(0f)
    val y = inset + scroll.value / scroll.maxValue.toFloat() * travel
    Box(
        Modifier
            .align(Alignment.TopEnd)
            .padding(end = 4.dp)
            .offset { IntOffset(0, y.toInt()) }
            .width(4.dp)
            .height(with(density) { thumb.toDp() })
            .clip(RoundedCornerShape(2.dp))
            .background(Color(0xFFB8C0CC)),
    )
}

@Composable
private fun ScreenTitle(title: String, caption: String? = null) {
    Text(title, color = Ink, fontSize = 26.sp, fontWeight = FontWeight.SemiBold)
    if (!caption.isNullOrBlank()) {
        Spacer(Modifier.height(6.dp))
        Text(caption, color = Muted, fontSize = 14.sp, lineHeight = 20.sp)
    }
}

@Composable
private fun Notice(text: String) {
    if (text.isBlank()) return
    Spacer(Modifier.height(12.dp))
    Text(text, color = Red, fontSize = 14.sp)
}

@Composable
private fun PromptCard(label: String, labelColor: Color, fill: Color, body: String) {
    Column(
        Modifier
            .fillMaxWidth()
            .clip(CardShape)
            .background(Color.White)
            .border(1.dp, Line, CardShape)
            .padding(14.dp),
    ) {
        Text(
            label,
            color = labelColor,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier
                .clip(RoundedCornerShape(8.dp))
                .background(fill)
                .padding(horizontal = 8.dp, vertical = 3.dp),
        )
        Spacer(Modifier.height(10.dp))
        Text(body, color = Ink, fontSize = 16.sp, lineHeight = 24.sp)
    }
}

@Composable
private fun StepRow(index: Int, text: String, chosen: Boolean, onClick: () -> Unit) {
    val numberBg = if (chosen) Blue else Color(0xFFEEF2F6)
    val numberFg = if (chosen) Color.White else Muted
    Row(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp)
            .clip(CardShape)
            .background(if (chosen) BlueSoft else Color.White)
            .border(1.dp, if (chosen) Blue else Line, CardShape)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(28.dp).clip(CircleShape).background(numberBg),
            contentAlignment = Alignment.Center,
        ) {
            Text("${index + 1}", color = numberFg, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        }
        Spacer(Modifier.width(12.dp))
        Text(text, color = Ink, fontSize = 15.sp, lineHeight = 22.sp, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun PrimaryButton(text: String, enabled: Boolean = true, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = Modifier.fillMaxWidth().height(50.dp),
        shape = RoundedCornerShape(14.dp),
    ) {
        Text(text, fontSize = 16.sp)
    }
}

@Composable
private fun QuietButton(text: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    OutlinedButton(
        onClick = onClick,
        modifier = modifier.height(48.dp),
        shape = RoundedCornerShape(14.dp),
    ) {
        Text(text, color = Ink)
    }
}

@Composable
private fun InputScreen(
    onStart: (String) -> Unit,
    onLoadRecords: ((List<RecordApi.Item>) -> Unit) -> Unit,
    onOpenRecord: (String) -> Unit,
    onDeleteRecord: (String, () -> Unit) -> Unit,
) {
    var task by rememberSaveable(RunSession.recordId) { mutableStateOf("") }
    WithRecords(onLoadRecords, onOpenRecord, onDeleteRecord) { openDrawer ->
        Page {
            TopActions(openDrawer)
            Spacer(Modifier.height(8.dp))
            ScreenTitle(stringResource(R.string.app_name), "写下要做的事，开始后右侧会出现操作条。")
            Spacer(Modifier.height(18.dp))
            OutlinedTextField(
                value = task,
                onValueChange = { task = it },
                modifier = Modifier.fillMaxWidth().heightIn(min = 180.dp),
                label = { Text("任务") },
                placeholder = { Text("例如：打开高德地图，搜索广州塔") },
                shape = RoundedCornerShape(16.dp),
                minLines = 5,
            )
            Notice(RunSession.notice)
            Spacer(Modifier.height(18.dp))
            PrimaryButton("开始", enabled = task.isNotBlank()) { onStart(task) }
        }
    }
}

@Composable
private fun ReviewScreen(
    onContinue: (String) -> Unit,
    onNew: () -> Unit,
    onLoadRecords: ((List<RecordApi.Item>) -> Unit) -> Unit,
    onOpenRecord: (String) -> Unit,
    onDeleteRecord: (String, () -> Unit) -> Unit,
) {
    var extra by rememberSaveable(RunSession.recordId) { mutableStateOf("") }
    WithRecords(onLoadRecords, onOpenRecord, onDeleteRecord) { openDrawer ->
        Page {
            TopActions(openDrawer) {
                Text(
                    "新建",
                    color = Blue,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.clickable(onClick = onNew).padding(vertical = 6.dp),
                )
            }
            Spacer(Modifier.height(8.dp))
            ScreenTitle(RunSession.recordName.ifBlank { "未命名" })
            Notice(RunSession.notice)
            Spacer(Modifier.height(16.dp))
            if (RunSession.dialogues.isEmpty()) {
                Text("还没有对话", color = Muted, fontSize = 14.sp)
            }
            RunSession.dialogues.forEach { dialogue ->
                DialogueFrame(dialogue)
                Spacer(Modifier.height(14.dp))
            }
            OutlinedTextField(
                value = extra,
                onValueChange = { extra = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("新提示词") },
                placeholder = { Text("不填则接着上一轮继续") },
                shape = RoundedCornerShape(16.dp),
                minLines = 2,
            )
            Spacer(Modifier.height(16.dp))
            PrimaryButton("继续") { onContinue(extra) }
        }
    }
}

@Composable
private fun DialogueFrame(dialogue: RunSession.Dialogue) {
    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(20.dp))
            .background(Color(0xFFF8F9FB))
            .border(1.dp, Line, RoundedCornerShape(20.dp))
            .padding(12.dp),
    ) {
        Text(
            "第 ${dialogue.index} 轮对话",
            color = Ink,
            fontSize = 16.sp,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(12.dp))
        PromptCard("原始提示词", Blue, BlueSoft, dialogue.prompt.ifBlank { "（空）" })
        dialogue.additions.forEach { item ->
            Spacer(Modifier.height(10.dp))
            val asking = item.text.startsWith("问：")
            PromptCard(
                label = if (asking) "第 ${item.progress} 步 · 提问" else "第 ${item.progress} 步 · 补充",
                labelColor = if (asking) Teal else Amber,
                fill = if (asking) TealSoft else AmberSoft,
                body = item.text,
            )
        }
        Spacer(Modifier.height(12.dp))
        Text("进度", color = Ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(8.dp))
        if (dialogue.progress.isEmpty()) {
            Text("还没有进度", color = Muted, fontSize = 14.sp)
        }
        dialogue.progress.forEachIndexed { index, item ->
            StepRow(index, item.text, false) {}
        }
        val writing = dialogue.reply.isBlank() && dialogue.index == RunSession.pendingReply
        if (dialogue.reply.isNotBlank() || writing) {
            Spacer(Modifier.height(12.dp))
            PromptCard(
                "回复",
                Reply,
                ReplySoft,
                if (writing) "正在写这次做了什么" else dialogue.reply,
            )
        }
    }
}

@Composable
private fun TopActions(onRecords: () -> Unit, trailing: @Composable () -> Unit = {}) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(
            "记录",
            color = Blue,
            fontSize = 15.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.clickable(onClick = onRecords).padding(vertical = 6.dp),
        )
        Spacer(Modifier.weight(1f))
        trailing()
    }
}

@Composable
private fun WithRecords(
    onLoadRecords: ((List<RecordApi.Item>) -> Unit) -> Unit,
    onOpenRecord: (String) -> Unit,
    onDeleteRecord: (String, () -> Unit) -> Unit,
    content: @Composable (openDrawer: () -> Unit) -> Unit,
) {
    var open by remember { mutableStateOf(false) }
    var records by remember { mutableStateOf(listOf<RecordApi.Item>()) }
    Box(Modifier.fillMaxSize()) {
        content {
            open = true
            onLoadRecords { records = it }
        }
        RecordDrawer(
            open = open,
            records = records,
            onClose = { open = false },
            onOpen = {
                open = false
                onOpenRecord(it)
            },
            onDelete = { id -> onDeleteRecord(id) { onLoadRecords { records = it } } },
        )
    }
}

@Composable
private fun RecordDrawer(
    open: Boolean,
    records: List<RecordApi.Item>,
    onClose: () -> Unit,
    onOpen: (String) -> Unit,
    onDelete: (String) -> Unit,
) {
    if (!open) return
    Box(Modifier.fillMaxSize()) {
        Box(
            Modifier
                .fillMaxSize()
                .background(Color(0x66010101))
                .clickable(onClick = onClose),
        )
        Column(
            Modifier
                .fillMaxHeight()
                .width(286.dp)
                .background(Color.White)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
        ) {
            Text("记录", color = Ink, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(12.dp))
            if (records.isEmpty()) {
                Text("还没有记录", color = Muted, fontSize = 14.sp)
            }
            records.forEach { item ->
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        item.name.ifBlank { "未命名" },
                        color = Ink,
                        fontSize = 16.sp,
                        modifier = Modifier.weight(1f).clickable { onOpen(item.id) },
                    )
                    Text(
                        "删除",
                        color = Red,
                        fontSize = 14.sp,
                        modifier = Modifier.clickable { onDelete(item.id) }.padding(start = 8.dp, top = 4.dp, bottom = 4.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun ReviseScreen(onApply: (Int, String) -> Unit, onCancel: () -> Unit, onEnd: () -> Unit) {
    var selected by remember(RunSession.phase, RunSession.progress.size) {
        mutableStateOf(RunSession.progress.lastIndex)
    }
    var addition by rememberSaveable { mutableStateOf("") }
    Page {
        ScreenTitle("修改指令")
        Spacer(Modifier.height(16.dp))
        PromptCard("原始提示词", Blue, BlueSoft, RunSession.task.ifBlank { "（空）" })
        RunSession.additions.forEach { item ->
            Spacer(Modifier.height(10.dp))
            val asking = item.text.startsWith("问：")
            PromptCard(
                label = if (asking) "第 ${item.progress} 步 · 提问" else "第 ${item.progress} 步 · 补充",
                labelColor = if (asking) Teal else Amber,
                fill = if (asking) TealSoft else AmberSoft,
                body = item.text,
            )
        }
        Spacer(Modifier.height(18.dp))
        Text("进度", color = Ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(8.dp))
        if (RunSession.progress.isEmpty()) {
            Text("还没有进度", color = Muted, fontSize = 14.sp)
        }
        RunSession.progress.forEachIndexed { index, item ->
            StepRow(index, item.text, index == selected) { selected = index }
        }
        Spacer(Modifier.height(16.dp))
        OutlinedTextField(
            value = addition,
            onValueChange = { addition = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("新提示词") },
            placeholder = { Text("不填则不修改") },
            shape = RoundedCornerShape(16.dp),
            minLines = 2,
        )
        Spacer(Modifier.height(16.dp))
        PrimaryButton("继续") { onApply(selected, addition) }
        Spacer(Modifier.height(8.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            QuietButton("先不改", Modifier.weight(1f), onCancel)
            QuietButton("结束", Modifier.weight(1f), onEnd)
        }
    }
}

@Composable
private fun AnswerScreen(onSubmit: (String) -> Unit, onEnd: () -> Unit) {
    var answer by rememberSaveable { mutableStateOf("") }
    Page {
        ScreenTitle("回答问题")
        Spacer(Modifier.height(16.dp))
        PromptCard("问题", Blue, BlueSoft, RunSession.question.ifBlank { "（空）" })
        Spacer(Modifier.height(14.dp))
        OutlinedTextField(
            value = answer,
            onValueChange = { answer = it },
            modifier = Modifier.fillMaxWidth().heightIn(min = 140.dp),
            label = { Text("回答") },
            shape = RoundedCornerShape(16.dp),
            minLines = 4,
        )
        Spacer(Modifier.height(16.dp))
        PrimaryButton("提交", enabled = answer.isNotBlank()) { onSubmit(answer) }
        Spacer(Modifier.height(8.dp))
        QuietButton("结束", Modifier.fillMaxWidth(), onEnd)
    }
}

@Composable
private fun OperatingScreen(onEnd: () -> Unit) {
    val phase = RunSession.phase
    val paused = phase == RunSession.Phase.Paused
    val risk = phase == RunSession.Phase.AwaitingRisk
    val refused = phase == RunSession.Phase.Refused
    val title = when {
        refused -> "无法完成"
        risk -> "当前页面危险"
        paused -> "已暂停"
        else -> "正在操作"
    }
    Page {
        ScreenTitle(title)
        if ((risk || refused) && RunSession.warning.isNotEmpty()) {
            Spacer(Modifier.height(14.dp))
            PromptCard(if (refused) "说明" else "风险", Red, RedSoft, RunSession.warning)
        }
        if (RunSession.task.isNotBlank()) {
            Spacer(Modifier.height(14.dp))
            PromptCard("任务", Blue, BlueSoft, RunSession.task)
        }
        Notice(RunSession.notice)
        Spacer(Modifier.height(20.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (risk) {
                Button(
                    onClick = { ScreenAgentService.instance?.acceptRisk() },
                    modifier = Modifier.weight(1f).height(50.dp),
                    shape = RoundedCornerShape(14.dp),
                ) {
                    Text("继续操作")
                }
            } else if (!refused) {
                Button(
                    onClick = { ScreenAgentService.instance?.togglePause() },
                    modifier = Modifier.weight(1f).height(50.dp),
                    shape = RoundedCornerShape(14.dp),
                ) {
                    Text(if (paused) "继续" else "暂停")
                }
            }
            QuietButton(if (risk) "终止结束" else "结束", Modifier.weight(1f), onEnd)
        }
    }
}
