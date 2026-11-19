package com.example.demo

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** The examples fill the field so the user can read and edit before submitting. */
@Composable
internal fun TaskPromptExamples(enabled: Boolean, onChoose: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("可以这样说", style = MaterialTheme.typography.titleMedium)
        listOf(
            "帮我扫这个二维码，打开网页。",
            "帮我扫码，把网页发邮件给女儿。",
            "在广州，从广州塔地铁站到客村地铁站怎么走？",
        ).forEach { example ->
            OutlinedButton(
                onClick = { onChoose(example) },
                enabled = enabled,
                modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
            ) {
                Text(example, fontSize = 18.sp)
            }
        }
    }
}
