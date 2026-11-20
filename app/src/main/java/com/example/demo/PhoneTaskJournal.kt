package com.example.demo

import android.content.Context
import android.text.format.DateFormat
import android.util.AtomicFile
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.paneTitle
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileNotFoundException
import java.util.Date
import java.util.UUID

internal enum class PhoneTaskOutcome(val label: String) {
    Started("已开始"),
    Waiting("待确认或补充"),
    HandedOff("已打开其他应用"),
    Completed("已完成"),
    UserConfirmed("用户确认完成"),
    Failed("未完成"),
    Cancelled("已取消"),
}

internal data class PhoneTaskRecord(
    val id: String,
    val instruction: String,
    val startedAtMillis: Long,
    val steps: List<String>,
    val outcome: PhoneTaskOutcome,
)

internal class PhoneTaskJournal(context: Context) {
    private val file = AtomicFile(File(context.noBackupFilesDir, "phone-task-journal.json"))

    suspend fun records(): List<PhoneTaskRecord> = withContext(Dispatchers.IO) {
        synchronized(fileLock) { readRecords() }
    }

    suspend fun begin(instruction: String): String = withContext(Dispatchers.IO) {
        val original = instruction.take(maxInstructionLength)
        require(original.isNotBlank())
        val record = PhoneTaskRecord(
            id = UUID.randomUUID().toString(),
            instruction = original,
            startedAtMillis = System.currentTimeMillis(),
            steps = emptyList(),
            outcome = PhoneTaskOutcome.Started,
        )
        synchronized(fileLock) {
            writeRecords((listOf(record) + readRecords()).take(maxRecords))
        }
        record.id
    }

    suspend fun note(id: String, message: String, outcome: PhoneTaskOutcome? = null): Unit =
        withContext(Dispatchers.IO) {
            val step = message.trim().take(maxStepLength)
            if (step.isEmpty() && outcome == null) return@withContext
            synchronized(fileLock) {
                val records = readRecords().toMutableList()
                val index = records.indexOfFirst { it.id == id }
                if (index == -1) return@synchronized
                val record = records[index]
                val updated = record.copy(
                    steps = if (step.isEmpty() || step == record.steps.lastOrNull()) record.steps
                    else (record.steps + step).takeLast(maxSteps),
                    outcome = outcome ?: record.outcome,
                )
                if (record != updated) {
                    records[index] = updated
                    writeRecords(records)
                }
            }
        }

    private fun readRecords(): List<PhoneTaskRecord> {
        val bytes = try {
            file.readFully()
        } catch (error: FileNotFoundException) {
            if (file.baseFile.exists()) throw error
            return emptyList()
        }
        val records = JSONArray(bytes.toString(Charsets.UTF_8))
        return List(minOf(records.length(), maxRecords)) { index ->
            val row = records.getJSONObject(index)
            val steps = row.getJSONArray("steps")
            PhoneTaskRecord(
                id = row.getString("id"),
                instruction = row.getString("instruction"),
                startedAtMillis = row.getLong("startedAtMillis"),
                steps = List(steps.length()) { steps.getString(it) }.takeLast(maxSteps),
                outcome = PhoneTaskOutcome.valueOf(row.getString("outcome")),
            )
        }
    }

    private fun writeRecords(records: List<PhoneTaskRecord>) {
        val rows = JSONArray()
        for (record in records) {
            rows.put(JSONObject().apply {
                put("id", record.id)
                put("instruction", record.instruction)
                put("startedAtMillis", record.startedAtMillis)
                put("steps", JSONArray(record.steps))
                put("outcome", record.outcome.name)
            })
        }
        val bytes = rows.toString().toByteArray(Charsets.UTF_8)
        val output = file.startWrite()
        try {
            output.write(bytes)
            file.finishWrite(output)
        } catch (error: Throwable) {
            file.failWrite(output)
            throw error
        }
    }

    private companion object {
        val fileLock = Any()
        const val maxRecords = 30
        const val maxSteps = 24
        const val maxInstructionLength = 1_500
        const val maxStepLength = 500
    }
}

@Composable
internal fun PhoneTaskJournalDialog(
    records: List<PhoneTaskRecord>,
    onDismiss: () -> Unit,
    onReuseInstruction: (String) -> Unit,
    initialRecordId: String? = null,
) {
    var selectedId by rememberSaveable(initialRecordId) { mutableStateOf(initialRecordId) }
    val historyListState = rememberLazyListState()
    val selected = records.firstOrNull { it.id == selectedId }
    val context = LocalContext.current
    val dateFormat = remember(context) { DateFormat.getDateFormat(context) }
    val timeFormat = remember(context) { DateFormat.getTimeFormat(context) }
    fun formattedTime(record: PhoneTaskRecord): String {
        val timestamp = Date(record.startedAtMillis)
        return "${dateFormat.format(timestamp)}  ${timeFormat.format(timestamp)}"
    }

    Dialog(onDismissRequest = { if (selected == null) onDismiss() else selectedId = null }) {
        Surface(
            modifier = Modifier.fillMaxWidth().fillMaxHeight(0.83f).semantics { paneTitle = "手机任务历史" },
            shape = MaterialTheme.shapes.extraLarge,
            tonalElevation = 4.dp,
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        if (selected == null) "手机任务历史" else "任务详情",
                        style = MaterialTheme.typography.headlineSmall,
                        modifier = Modifier.weight(1f).semantics { heading() },
                    )
                    TextButton(onClick = onDismiss) { Text("关闭", fontSize = 17.sp) }
                }
                when {
                    selected != null -> {
                        TextButton(onClick = { selectedId = null }) { Text("返回列表", fontSize = 17.sp) }
                        LazyColumn(
                            modifier = Modifier.weight(1f),
                            verticalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            item { PhoneTaskJournalMetadata(selected, formattedTime(selected)) }
                            item { Text(selected.instruction, fontSize = 19.sp) }
                            if (selected.steps.isNotEmpty()) {
                                item { HorizontalDivider() }
                                item {
                                    Text(
                                        "处理记录",
                                        style = MaterialTheme.typography.titleMedium,
                                        modifier = Modifier.semantics { heading() },
                                    )
                                }
                                items(selected.steps) { step -> Text(step, fontSize = 17.sp) }
                            }
                        }
                        Button(
                            onClick = { onReuseInstruction(selected.instruction) },
                            modifier = Modifier.fillMaxWidth().heightIn(min = 54.dp),
                        ) { Text("填回原指令", fontSize = 18.sp) }
                    }
                    records.isEmpty() -> Text("暂无手机任务记录。", fontSize = 18.sp)
                    else -> LazyColumn(
                        state = historyListState,
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        items(records, key = { it.id }) { record ->
                            OutlinedCard(
                                onClick = { selectedId = record.id },
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Column(
                                    modifier = Modifier.padding(12.dp),
                                    verticalArrangement = Arrangement.spacedBy(4.dp),
                                ) {
                                    Text(
                                        record.instruction,
                                        fontSize = 18.sp,
                                        maxLines = 2,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                    PhoneTaskJournalMetadata(record, formattedTime(record))
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun PhoneTaskJournalMetadata(record: PhoneTaskRecord, timestamp: String) {
    Text(
        "${record.outcome.label} · $timestamp",
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        style = MaterialTheme.typography.bodyMedium,
    )
}
