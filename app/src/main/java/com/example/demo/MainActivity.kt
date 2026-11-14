package com.example.demo

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import com.example.demo.ui.theme.DemoTheme

class MainActivity : ComponentActivity() {
    private val sharedScan = mutableStateOf<SharedScan?>(null)
    private var shareId = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) receiveShare(intent)
        enableEdgeToEdge()
        setContent {
            DemoTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { padding ->
                    WorkflowScreen(Modifier.padding(padding), sharedScan.value) { sharedScan.value = null }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        receiveShare(intent)
    }

    private fun receiveShare(intent: Intent) {
        if (intent.action != Intent.ACTION_SEND || intent.type != "text/plain") return
        val text = runCatching { intent.getCharSequenceExtra(Intent.EXTRA_TEXT) as? String }.getOrNull()
        sharedScan.value = SharedScan(++shareId, externalScanUrl(text))
    }
}
