package com.example.demo

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.example.demo.ui.theme.DemoTheme
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

private const val EMULATOR_HOST = "your_ip"
private const val REAL_DEVICE_HOST = "your_ip"
private const val PORT = 8000

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            DemoTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
                    DemoScreen(modifier = Modifier.padding(innerPadding))
                }
            }
        }
    }
}

private fun apiHost(): String {
    val fingerprint = android.os.Build.FINGERPRINT
    val isEmulator = fingerprint.contains("generic")
        || fingerprint.contains("emulator")
        || android.os.Build.HARDWARE.contains("ranchu")
        || android.os.Build.HARDWARE.contains("goldfish")
        || android.os.Build.MODEL.contains("Emulator")
        || android.os.Build.MODEL.contains("SDK")
    return if (isEmulator) EMULATOR_HOST else REAL_DEVICE_HOST
}

@Composable
fun DemoScreen(modifier: Modifier = Modifier) {
    var result by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    val context = LocalContext.current

    Column(
        modifier = modifier.padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Button(
            onClick = {
                context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            },
        ) {
            Text("打开无障碍设置")
        }
        Button(
            enabled = !loading,
            onClick = {
                loading = true
                result = "请求中..."
                Thread {
                    val text = try {
                        val connection = (URL("http://${apiHost()}:$PORT/gmail/start").openConnection() as HttpURLConnection).apply {
                            requestMethod = "POST"
                            connectTimeout = 5000
                            readTimeout = 5000
                            doOutput = true
                        }
                        val code = connection.responseCode
                        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                        val body = stream.bufferedReader().use { it.readText() }
                        connection.disconnect()
                        val json = JSONObject(body)
                        json.optString("message")
                    } catch (error: Exception) {
                        "连接失败：${error.message}"
                    }
                    Handler(Looper.getMainLooper()).post {
                        result = text
                        loading = false
                    }
                }.start()
            },
        ) {
            Text("Gmail 固定流程")
        }
        if (result.isNotEmpty()) {
            Text(result)
        }
    }
}
