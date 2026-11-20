package com.example.demo

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import com.example.demo.ui.theme.DemoTheme

class MainActivity : ComponentActivity() {
    private val sharedScan = mutableStateOf<SharedScan?>(null)
    private var shareId = 0
    private var historyRequestSequence = 0
    private val historyOpenRequest = mutableStateOf<TaskHistoryOpenRequest?>(null)
    private val phoneHistoryRequest = mutableStateOf<Int?>(null)
    private val phoneRevisionRequest = mutableStateOf<String?>(null)
    private val computerMode = mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState != null) {
            computerMode.value = savedInstanceState.getBoolean("computer_mode")
            shareId = savedInstanceState.getInt("share_sequence")
            if (savedInstanceState.getBoolean("share_pending")) {
                sharedScan.value = SharedScan(savedInstanceState.getInt("share_id"), savedInstanceState.getString("share_url"))
            }
        }
        if (!receivePhoneOverlay(intent) && savedInstanceState == null) {
            receiveShare(intent)
            receiveHistoryRequest(intent)
        }
        enableEdgeToEdge()
        setContent {
            DemoTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { padding ->
                    if (computerMode.value || historyOpenRequest.value != null) {
                        Column(Modifier.padding(padding)) {
                            TextButton(onClick = {
                                historyOpenRequest.value = null
                                computerMode.value = false
                            }) { Text("返回手机模式") }
                            WorkflowScreen(Modifier.fillMaxSize(), sharedScan.value, historyOpenRequest.value) {
                                sharedScan.value = null
                            }
                        }
                    } else {
                        PhoneWorkflowScreen(
                            Modifier.padding(padding), sharedScan.value,
                            onDismissShare = { sharedScan.value = null },
                            onUseComputer = { PhoneTaskOverlay.clear(); computerMode.value = true },
                            historyRequest = phoneHistoryRequest.value,
                            onHistoryOpened = { phoneHistoryRequest.value = null },
                            revisionRequest = phoneRevisionRequest.value,
                            onRevisionOpened = { phoneRevisionRequest.value = null },
                        )
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (receivePhoneOverlay(intent)) return
        receiveShare(intent)
        receiveHistoryRequest(intent)
    }

    private fun receivePhoneOverlay(intent: Intent): Boolean {
        val destination = PhoneTaskOverlay.consumeOpenRequest(intent) ?: return false
        computerMode.value = false
        historyOpenRequest.value = null
        when (destination) {
            PhoneOverlayDestination.Assistant -> Unit
            PhoneOverlayDestination.History -> phoneHistoryRequest.value = ++historyRequestSequence
            PhoneOverlayDestination.Revision -> phoneRevisionRequest.value = (++historyRequestSequence).toString()
        }
        return true
    }

    private fun receiveHistoryRequest(intent: Intent) {
        val id = intent.getStringExtra(TaskHistoryEntry.EXTRA_TASK_ID).orEmpty()
        val prefs = getSharedPreferences("silverguide", MODE_PRIVATE)
        if (id.isNotBlank() && id == prefs.getString("task_id", "") && id != prefs.getString("dismissed_task", "")) {
            historyOpenRequest.value = TaskHistoryOpenRequest(id, ++historyRequestSequence)
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("computer_mode", computerMode.value)
        outState.putInt("share_sequence", shareId)
        sharedScan.value?.let {
            outState.putBoolean("share_pending", true)
            outState.putInt("share_id", it.id)
            outState.putString("share_url", it.url)
        }
        super.onSaveInstanceState(outState)
    }

    private fun receiveShare(intent: Intent) {
        if (intent.action != Intent.ACTION_SEND || intent.type != "text/plain") return
        val text = runCatching { intent.getCharSequenceExtra(Intent.EXTRA_TEXT) as? String }.getOrNull()
        sharedScan.value = SharedScan(++shareId, externalScanUrl(text))
    }
}
