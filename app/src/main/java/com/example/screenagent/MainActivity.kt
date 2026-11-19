package com.example.screenagent

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                if (RunSession.phase == RunSession.Phase.Input) {
                    InputScreen(onStart = ::start)
                } else {
                    OperatingScreen(onEnd = ::end)
                }
            }
        }
    }

    private fun start(task: String) {
        val text = task.trim()
        if (text.isEmpty()) {
            RunSession.notice = "请先输入要做的事"
            return
        }
        val blocked = RunSession.startBlockedReason(this)
        if (blocked != null) {
            RunSession.notice = blocked
            if (ScreenAgentService.instance == null) {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
            return
        }
        RunSession.notice = ""
        RunSession.task = text
        RunSession.phase = RunSession.Phase.Operating
        val shown = ScreenAgentService.instance?.showOverlay() == true
        if (!shown) {
            RunSession.phase = RunSession.Phase.Input
            RunSession.notice = "无法显示操作条"
            return
        }
        val server = getSharedPreferences("screen-agent", MODE_PRIVATE)
            .getString("server", "http://127.0.0.1:8000")
            ?: "http://127.0.0.1:8000"
        ScreenAgentService.instance?.begin(text, server)
        startActivity(Intent(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_HOME)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        })
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
private fun InputScreen(onStart: (String) -> Unit) {
    var task by rememberSaveable { mutableStateOf(RunSession.task) }
    Column(Modifier.fillMaxSize().padding(20.dp).imePadding()) {
        Text(stringResource(R.string.app_name), style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(8.dp))
        Text("开始后右侧有一个小操作条。按住「操作中」可以拖走，点一下展开进度。")
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            value = task,
            onValueChange = { task = it },
            modifier = Modifier.fillMaxWidth().weight(1f),
            label = { Text("你想要做什么") },
            placeholder = { Text("例如：打开高德地图，搜索广州塔") },
        )
        if (RunSession.notice.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            Text(RunSession.notice, color = MaterialTheme.colorScheme.error)
        }
        Spacer(Modifier.height(12.dp))
        Button(
            onClick = { onStart(task) },
            modifier = Modifier.fillMaxWidth(),
            enabled = task.isNotBlank(),
        ) {
            Text("开始")
        }
    }
}

@Composable
private fun OperatingScreen(onEnd: () -> Unit) {
    val paused = RunSession.phase == RunSession.Phase.Paused
    val shape = RoundedCornerShape(12.dp)
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Text(
            if (paused) "已暂停" else "正在操作中",
            style = MaterialTheme.typography.titleLarge,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            RunSession.task,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (RunSession.notice.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            Text(RunSession.notice, color = MaterialTheme.colorScheme.error)
        }
        Spacer(Modifier.height(12.dp))
        Box(
            Modifier
                .fillMaxWidth()
                .weight(1f)
                .clip(shape)
                .background(MaterialTheme.colorScheme.surfaceVariant)
                .border(1.dp, MaterialTheme.colorScheme.outline, shape),
        )
        Spacer(Modifier.height(12.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = { ScreenAgentService.instance?.togglePause() },
                modifier = Modifier.weight(1f),
            ) {
                Text(if (paused) "继续" else "暂停")
            }
            OutlinedButton(onClick = onEnd, modifier = Modifier.weight(1f)) {
                Text("结束")
            }
        }
    }
}
