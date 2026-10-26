package com.example.demo

import android.Manifest
import android.content.pm.PackageManager
import android.media.MediaRecorder
import android.os.Build
import android.util.Base64
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.util.UUID

@Composable
fun LocalVoiceInput(enabled: Boolean, onRecognized: (String) -> Unit, onMessage: (String) -> Unit) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val scope = rememberCoroutineScope()
    val currentRecognized by rememberUpdatedState(onRecognized)
    val currentMessage by rememberUpdatedState(onMessage)
    var recorder by remember { mutableStateOf<MediaRecorder?>(null) }
    var clip by remember { mutableStateOf<File?>(null) }
    var recording by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var ready by remember { mutableStateOf(false) }
    var remaining by remember { mutableIntStateOf(20) }
    var status by remember { mutableStateOf("正在连接语音功能…") }
    var transcription by remember { mutableStateOf<Job?>(null) }

    fun cancelRecording() {
        recording = false
        recorder?.let { try { it.stop() } catch (_: Exception) { }; it.release() }
        recorder = null
        clip?.delete(); clip = null
    }

    fun finishRecording() {
        if (!recording) return
        recording = false
        val saved = clip
        try { recorder?.stop() }
        catch (_: Exception) {
            recorder?.release(); recorder = null; saved?.delete()
            status = "没有录到完整声音，请重新录制。"
            return
        }
        recorder?.release(); recorder = null
        if (saved == null) { status = "没有取得录音，请重新录制。"; return }
        busy = true; status = "正在识别您的话…"
        transcription = scope.launch {
            val thisJob = coroutineContext[Job]
            try {
                val encoded = withContext(Dispatchers.IO) {
                    require(saved.length() in 32..524288) { "录音无效，请重新录制一句话。" }
                    Base64.encodeToString(saved.readBytes(), Base64.NO_WRAP)
                }
                val result = request("/speech/transcribe", JSONObject().put("audio_base64", encoded), "POST")
                val text = result.getString("text")
                status = "已转成文字，请核对下面的信息。"
                currentRecognized(text)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                status = e.message ?: "识别未完成，请重试或使用文字输入。"
                currentMessage(status)
            } finally {
                saved.delete()
                if (clip === saved) clip = null
                if (transcription === thisJob) { busy = false; transcription = null }
            }
        }
    }

    @Suppress("DEPRECATION")
    fun startRecording() {
        if (!enabled || recording || busy || !ready) return
        val saved = File(context.cacheDir, "silverguide-voice-${UUID.randomUUID()}.m4a")
        try {
            val audio = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(context) else MediaRecorder()
            recorder = audio; clip = saved
            audio.setAudioSource(MediaRecorder.AudioSource.MIC)
            audio.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            audio.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            audio.setAudioEncodingBitRate(64000)
            audio.setAudioSamplingRate(16000)
            audio.setOutputFile(saved.absolutePath)
            audio.setOnErrorListener { _, _, _ -> scope.launch {
                cancelRecording(); status = "麦克风录音中断，请重试或使用文字输入。"
            } }
            audio.prepare(); audio.start()
            remaining = 20; recording = true; status = "正在录音，请说话。"
        } catch (_: Exception) {
            cancelRecording(); status = "无法开始录音，请检查麦克风权限后重试。"
            currentMessage(status)
        }
    }

    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startRecording() else { status = "请允许麦克风权限，也可以使用文字输入。"; currentMessage(status) }
    }
    LaunchedEffect(recording) {
        if (recording) {
            repeat(20) { delay(1000); remaining = 19 - it }
            finishRecording()
        }
    }
    LaunchedEffect(lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                if (!recording && !busy) try {
                    val response = request("/speech/status")
                    val available = response.optBoolean("available")
                    if (!ready || !available) status = response.optString("message")
                    ready = available
                } catch (e: CancellationException) { throw e }
                catch (_: Exception) { ready = false; status = "请连接电脑后再使用语音，也可以使用文字输入。" }
                delay(10000)
            }
        }
    }
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_STOP) {
                if (recording) { cancelRecording(); status = "录音已停止，请保持助手页面打开后再试。" }
                if (busy) {
                    transcription?.cancel(); transcription = null; busy = false; cancelRecording()
                    status = "本次识别已取消，可以重新录音。"
                }
            }
        }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer); transcription?.cancel(); cancelRecording() }
    }

    Button(onClick = {
        if (recording) finishRecording()
        else if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) startRecording()
        else permission.launch(Manifest.permission.RECORD_AUDIO)
    }, enabled = enabled && !busy && (recording || ready), modifier = Modifier.fillMaxWidth().heightIn(min = 64.dp)) {
        Text(if (recording) "说完了，转成文字" else if (busy) "正在识别…" else "说出您的需求", fontSize = 21.sp)
    }
    Text(if (recording) "正在录音，请说话。还剩 $remaining 秒。" else status, fontSize = 18.sp)
    if (recording || busy) OutlinedButton(onClick = {
        transcription?.cancel(); transcription = null; busy = false; cancelRecording(); status = "本次语音输入已取消。"
    }, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) { Text(if (recording) "取消录音" else "取消识别", fontSize = 20.sp) }
    else Text("最多录制 20 秒，说完后点击转文字；录音由连接的电脑本地识别。", fontSize = 16.sp)
}
