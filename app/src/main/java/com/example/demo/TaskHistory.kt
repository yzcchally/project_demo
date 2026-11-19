package com.example.demo

import android.content.Context
import android.content.Intent
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.paneTitle
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Locale

internal object TaskHistoryEntry {
    const val EXTRA_OPEN_HISTORY = "com.example.demo.OPEN_TASK_HISTORY"

    fun open(context: Context) {
        context.startActivity(Intent(context, MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
            putExtra(EXTRA_OPEN_HISTORY, true)
        })
    }
}

private data class TaskHistoryStep(
    val seq: Long,
    val message: String,
    val time: String,
    val verifiedEvidence: List<String>,
)

private data class ReturnOption(val label: String, val description: String, val revision: Long)

private val evidenceLabels = mapOf(
    "webpage_verified" to "网址已核对",
    "draft_verified" to "邮件草稿已核对",
    "sent_record_verified" to "已发送记录已核对",
    "delivery_confirmed" to "收件人已确认收到",
    "route_verified" to "高德路线已核对",
)

private fun parseSteps(array: JSONArray): List<TaskHistoryStep> = List(array.length()) { index ->
    val row = array.getJSONObject(index)
    val evidence = row.optJSONObject("evidence")
    TaskHistoryStep(
        seq = row.getLong("seq"),
        message = row.optString("message").ifBlank { "任务状态已更新" },
        time = formatStepTime(row.optString("time")),
        verifiedEvidence = evidenceLabels.filterKeys { evidence?.optBoolean(it) == true }.values.toList(),
    )
}

private fun formatStepTime(time: String): String {
    if (time.isBlank()) return ""
    val seconds = time.replace(Regex("""\.\d+(?=Z|[+-])"""), "")
    val formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.ROOT)
    val date = runCatching { formatter.parse(seconds) }.getOrNull() ?: return time
    return SimpleDateFormat("yyyy年M月d日 HH:mm", Locale.SIMPLIFIED_CHINESE).format(date)
}

@Composable
internal fun TaskHistoryScaffold(
    modifier: Modifier,
    scrollState: ScrollState,
    onOpen: () -> Unit,
    content: @Composable ColumnScope.() -> Unit,
) {
    Box(modifier.fillMaxSize()) {
        Column(
            modifier = Modifier.fillMaxSize().verticalScroll(scrollState)
                .padding(start = 20.dp, end = 62.dp, top = 20.dp, bottom = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
            content = content,
        )
        Button(
            modifier = Modifier.align(Alignment.CenterEnd).heightIn(min = 56.dp),
            onClick = onOpen,
            shape = RoundedCornerShape(topStart = 22.dp, bottomStart = 22.dp),
            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 12.dp),
        ) { Text("历史", fontSize = 16.sp) }
    }
}

@Composable
internal fun TaskHistoryPanel(
    taskId: String,
    canReturn: Boolean,
    onDismiss: () -> Unit,
    onReturned: (JSONObject) -> Unit,
) {
    var allSteps by remember(taskId) { mutableStateOf(emptyList<TaskHistoryStep>()) }
    var selection by remember(taskId) { mutableStateOf<TaskHistoryStep?>(null) }
    var retry by remember(taskId) { mutableIntStateOf(0) }
    var loading by remember(taskId) { mutableStateOf(true) }
    var historyError by remember(taskId) { mutableStateOf("") }
    var checking by remember(taskId) { mutableStateOf(false) }
    var returnOption by remember(taskId) { mutableStateOf<ReturnOption?>(null) }
    var stepError by remember(taskId) { mutableStateOf("") }
    var returning by remember(taskId) { mutableStateOf(false) }
    val historyScroll = remember(taskId) { LazyListState() }
    val scope = rememberCoroutineScope()
    val selected = selection
    val selectedNumber = allSteps.indexOfFirst { it.seq == selected?.seq } + 1

    LaunchedEffect(taskId, retry) {
        if (taskId.isBlank()) {
            loading = false
            return@LaunchedEffect
        }
        historyError = ""
        try {
            var cursor = allSteps.lastOrNull()?.seq ?: 0L
            do {
                val response = request("/api/tasks/$taskId/events?after=$cursor")
                val batch = parseSteps(response.getJSONArray("events"))
                if (batch.isNotEmpty()) {
                    allSteps = allSteps + batch
                    cursor = batch.last().seq
                }
                loading = false
                if (batch.size < 100) delay(2000)
            } while (true)
        } catch (error: CancellationException) {
            throw error
        } catch (_: Exception) {
            historyError = "暂时读不到新的步骤，请检查连接后重试。"
        } finally {
            loading = false
        }
    }

    LaunchedEffect(taskId, selected?.seq, canReturn, retry, allSteps.lastOrNull()?.seq) {
        returnOption = null
        stepError = ""
        if (selected == null || !canReturn) return@LaunchedEffect
        checking = true
        try {
            val response = request("/api/tasks/$taskId/return-step/${selected.seq}")
            response.optJSONObject("action")?.let {
                returnOption = ReturnOption(it.getString("label"), it.getString("description"), response.getLong("revision"))
            }
        } catch (error: CancellationException) {
            throw error
        } catch (_: Exception) {
            stepError = "暂时无法核对这一步能否返回。"
        } finally {
            checking = false
        }
    }

    Dialog(
        onDismissRequest = { if (!returning) onDismiss() },
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Box(Modifier.fillMaxSize().padding(vertical = 12.dp), contentAlignment = Alignment.CenterEnd) {
            Surface(
                color = MaterialTheme.colorScheme.surface,
                shape = RoundedCornerShape(topStart = 24.dp, bottomStart = 24.dp),
                tonalElevation = 5.dp,
                modifier = Modifier.widthIn(max = 440.dp).fillMaxWidth(0.94f).fillMaxHeight()
                    .semantics { paneTitle = "任务历史" },
            ) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text("任务历史", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
                        TextButton(onClick = onDismiss, enabled = !returning) { Text("关闭", fontSize = 18.sp) }
                    }
                    HorizontalDivider()
                    if (selected != null) {
                        TextButton(
                            onClick = { selection = null },
                            enabled = !returning,
                            modifier = Modifier.align(Alignment.Start),
                        ) { Text("返回步骤列表", fontSize = 18.sp) }
                        LazyColumn(
                            modifier = Modifier.weight(1f),
                            verticalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            item {
                                Text("第 ${selectedNumber} 步", style = MaterialTheme.typography.titleLarge)
                            }
                            item { Text(selected.message, fontSize = 21.sp) }
                            if (selected.time.isNotBlank()) item {
                                Text(selected.time, color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 15.sp)
                            }
                            selected.verifiedEvidence.forEach { label -> item { Text(label, fontSize = 17.sp) } }
                        }
                        val option = returnOption
                        when {
                            checking -> Text("正在核对可用操作…", fontSize = 16.sp)
                            stepError.isNotBlank() -> {
                                Text(stepError, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
                                OutlinedButton(onClick = { retry++ }, enabled = !returning) { Text("重新核对", fontSize = 18.sp) }
                            }
                            option != null && canReturn -> {
                                Text(option.description, fontSize = 17.sp)
                                Button(
                                    onClick = {
                                        returning = true
                                        scope.launch {
                                            try {
                                                val response = request(
                                                    "/api/tasks/$taskId/return-step/${selected.seq}",
                                                    JSONObject().put("expected_revision", option.revision),
                                                    "POST",
                                                )
                                                onReturned(response)
                                            } catch (error: CancellationException) {
                                                throw error
                                            } catch (error: Exception) {
                                                stepError = error.message ?: "暂时无法返回，请重新核对任务状态。"
                                            } finally {
                                                returning = false
                                            }
                                        }
                                    },
                                    enabled = !returning,
                                    modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp),
                                ) { Text(if (returning) "正在返回…" else option.label, fontSize = 19.sp) }
                            }
                            else -> Text("此步骤可以查看记录。", fontSize = 17.sp)
                        }
                    } else {
                        when {
                            historyError.isNotBlank() -> {
                                Text(historyError, color = MaterialTheme.colorScheme.error, fontSize = 17.sp)
                                OutlinedButton(onClick = { retry++ }) { Text("重新读取", fontSize = 18.sp) }
                            }
                            loading -> Text("正在读取任务步骤…", fontSize = 18.sp)
                            allSteps.isEmpty() -> Text("还没有任务步骤。", fontSize = 18.sp)
                            else -> Text("共 ${allSteps.size} 步", fontSize = 17.sp,
                                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                        }
                        LazyColumn(modifier = Modifier.weight(1f), state = historyScroll) {
                            itemsIndexed(allSteps, key = { _, step -> step.seq }) { index, step ->
                                Row(
                                    modifier = Modifier.fillMaxWidth()
                                        .heightIn(min = 56.dp)
                                        .clickable(onClickLabel = "查看第 ${index + 1} 步") {
                                            returnOption = null
                                            stepError = ""
                                            checking = canReturn
                                            selection = step
                                        }
                                        .padding(vertical = 12.dp),
                                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                                    verticalAlignment = Alignment.Top,
                                ) {
                                    Text("${index + 1}", color = MaterialTheme.colorScheme.primary, fontSize = 18.sp)
                                    Text(step.message, fontSize = 19.sp, modifier = Modifier.weight(1f))
                                }
                                HorizontalDivider()
                            }
                        }
                    }
                }
            }
        }
    }
}
