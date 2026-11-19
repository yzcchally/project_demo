package com.example.demo

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
internal fun TaskInstructionEntry(
    instruction: String,
    loading: Boolean,
    startLabel: String,
    onInstructionChange: (String) -> Unit,
    onChooseExample: (String) -> Unit,
    onStart: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(
            value = instruction,
            onValueChange = onInstructionChange,
            label = { Text("告诉小银想做什么") },
            textStyle = MaterialTheme.typography.bodyLarge.copy(fontSize = 19.sp),
            modifier = Modifier.fillMaxWidth(),
            minLines = 2,
            maxLines = 4,
        )
        Button(
            onClick = onStart,
            enabled = !loading && instruction.isNotBlank(),
            modifier = Modifier.fillMaxWidth().heightIn(min = 60.dp),
        ) {
            Text(if (loading) "正在提交…" else startLabel, fontSize = 21.sp)
        }
        if (instruction.isBlank()) TaskPromptExamples(enabled = !loading, onChoose = onChooseExample)
    }
}

private val taskPromptExamples = listOf(
    "扫码打开网页" to "帮我扫这个二维码，打开网页。",
    "扫码发邮件" to "帮我扫码，把网页发邮件给女儿。",
    "公交地铁导航" to DemoCases.navigationExample,
)

/** The examples fill the field so the user can read and edit before submitting. */
@Composable
private fun TaskPromptExamples(enabled: Boolean, onChoose: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("点一个示例填入", fontSize = 17.sp)
        taskPromptExamples.forEach { (label, instruction) ->
            OutlinedButton(
                onClick = { onChoose(instruction) },
                enabled = enabled,
                modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
            ) {
                Text(label, fontSize = 18.sp)
            }
        }
    }
}
