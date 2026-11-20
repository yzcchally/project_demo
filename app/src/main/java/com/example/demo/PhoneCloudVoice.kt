package com.example.demo

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.SystemClock
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject

private const val voiceSeconds = 20
private const val voiceSampleRate = 16_000
private const val voiceBytesPerSample = 2
private const val voiceReadSamples = voiceSampleRate / 10
private const val minimumVoiceSamples = voiceSampleRate / 4
private const val voiceResponseLimit = 65_536L

private enum class VoiceStage { Idle, Recording, Transcribing }

private class VoiceInputError(message: String) : Exception(message)

internal fun PhoneCloudConfig?.speechCredential(
    provider: PhoneSpeechProvider = this?.speechProvider ?: PhoneSpeechProvider.Zhipu,
): String {
    val config = this ?: return ""
    return when (provider) {
        PhoneSpeechProvider.Zhipu -> config.speechKey.ifBlank {
            if (config.chatProvider == PhoneChatProvider.Zhipu) config.chatKey else ""
        }
        PhoneSpeechProvider.SiliconFlow -> config.siliconFlowSpeechKey
    }
}

@Composable
internal fun PhoneCloudVoiceInput(
    settings: PhoneCloudConfig?,
    enabled: Boolean,
    onRecognized: (String) -> Unit,
    idleLabel: String = "用语音",
    successMessage: String = "已转成文字，请核对后开始任务。",
    showProvider: Boolean = false,
) {
    val context = LocalContext.current
    val app = context.applicationContext
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val scope = rememberCoroutineScope()
    val recognized by rememberUpdatedState(onRecognized)
    val localVoice = remember(app) { PhoneOnDeviceSpeech.isAvailable(app) }
    val provider = settings?.speechProvider ?: PhoneSpeechProvider.Zhipu
    val speechKey = settings.speechCredential(provider).takeIf(PhoneCloudModel::validCredential).orEmpty()
    val currentProvider by rememberUpdatedState(provider)
    val currentKey by rememberUpdatedState(speechKey)
    val currentEnabled by rememberUpdatedState(enabled)
    var stage by remember { mutableStateOf(VoiceStage.Idle) }
    var status by remember { mutableStateOf("") }
    var remaining by remember { mutableIntStateOf(voiceSeconds) }
    var operation by remember { mutableStateOf<Job?>(null) }
    var finishRecording by remember { mutableStateOf<AtomicBoolean?>(null) }

    fun cancel(message: String) {
        operation?.cancel()
        status = message
    }

    fun start() {
        val selectedProvider = currentProvider
        val key = currentKey
        val useCloud = key.isNotEmpty()
        if (!currentEnabled || (!localVoice && !useCloud) || operation != null ||
            !lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)
        ) return

        val finished = AtomicBoolean()
        finishRecording = finished
        remaining = voiceSeconds
        stage = VoiceStage.Recording
        status = "正在录音，请说话。"
        operation = scope.launch {
            try {
                val samples = withContext(Dispatchers.IO) { recordVoice(finished) }
                if (!currentEnabled || currentProvider != selectedProvider || currentKey != key) {
                    throw CancellationException("Voice input settings changed")
                }
                stage = VoiceStage.Transcribing
                status = if (useCloud && showProvider) "${selectedProvider.label}正在转写…" else "正在转成文字…"
                val transcript = if (useCloud) {
                    transcribePhoneVoice(encodeVoiceWave(samples, samples.size), selectedProvider, key)
                } else {
                    PhoneOnDeviceSpeech.transcribe(app, samples)
                }
                if (transcript.isBlank()) throw VoiceInputError("没有听清，请靠近手机再说一次。")
                if (currentEnabled && currentProvider == selectedProvider && currentKey == key) {
                    status = successMessage
                    recognized(transcript)
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                status = when (error) {
                    is VoiceInputError -> if (useCloud && !showProvider)
                        error.message.orEmpty().removePrefix(selectedProvider.label) else error.message.orEmpty()
                    is SecurityException -> "无法使用麦克风，请检查麦克风权限。"
                    is IOException -> if (useCloud) "在线语音暂时连接失败，请检查手机网络后重试。"
                        else "手机暂时无法转写，请重新录音。"
                    else -> "语音输入暂未完成，请重新录音。"
                }
            } finally {
                finishRecording = null
                stage = VoiceStage.Idle
                operation = null
            }
        }
    }

    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            start()
        } else {
            status = "请允许麦克风权限，也可以直接输入文字。"
        }
    }

    LaunchedEffect(stage) {
        if (stage == VoiceStage.Recording) {
            repeat(voiceSeconds) {
                delay(1000)
                remaining = voiceSeconds - it - 1
            }
        }
    }
    LaunchedEffect(provider, speechKey) {
        if (operation != null) cancel("本次语音输入已取消。")
        else status = ""
    }
    LaunchedEffect(enabled) {
        if (!enabled && operation != null) cancel("本次语音输入已取消。")
    }
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_STOP && operation != null) {
                cancel("本次语音输入已停止，请保持助手页面打开后重试。")
            }
        }
        lifecycle.addObserver(observer)
        onDispose {
            lifecycle.removeObserver(observer)
            operation?.cancel()
        }
    }

    Button(
        onClick = {
            if (stage == VoiceStage.Recording) {
                finishRecording?.set(true)
            } else if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
            ) {
                start()
            } else {
                permission.launch(Manifest.permission.RECORD_AUDIO)
            }
        },
        enabled = stage == VoiceStage.Recording ||
            (enabled && (localVoice || speechKey.isNotBlank()) && stage == VoiceStage.Idle),
        modifier = Modifier.fillMaxWidth().heightIn(min = 64.dp),
    ) {
        Text(
            when (stage) {
                VoiceStage.Idle -> idleLabel
                VoiceStage.Recording -> "说完了，转成文字"
                VoiceStage.Transcribing -> "正在转写…"
            },
            fontSize = 21.sp,
        )
    }
    if (stage == VoiceStage.Recording) {
        Text("正在录音，请说话。还剩 $remaining 秒。", fontSize = 18.sp)
    } else if (status.isNotBlank()) {
        Text(status, fontSize = 18.sp, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
    }
    if (showProvider && stage == VoiceStage.Idle && speechKey.isNotBlank()) {
        Text(
            "${provider.label}在线转写。结束录音后会把这段录音交给${provider.label}。",
            fontSize = 16.sp,
        )
    }
    if (stage != VoiceStage.Idle) {
        OutlinedButton(
            onClick = { cancel("本次语音输入已取消。") },
            modifier = Modifier.fillMaxWidth().heightIn(min = 54.dp),
        ) {
            Text(if (stage == VoiceStage.Recording) "取消录音" else "取消转写", fontSize = 18.sp)
        }
    }
}

@SuppressLint("MissingPermission")
private suspend fun recordVoice(finished: AtomicBoolean): ShortArray {
    val channel = AudioFormat.CHANNEL_IN_MONO
    val encoding = AudioFormat.ENCODING_PCM_16BIT
    val requiredBuffer = AudioRecord.getMinBufferSize(voiceSampleRate, channel, encoding)
    if (requiredBuffer <= 0) throw VoiceInputError("手机麦克风暂时无法录音。")
    val microphone = AudioRecord.Builder()
        .setAudioSource(MediaRecorder.AudioSource.MIC)
        .setAudioFormat(
            AudioFormat.Builder().setSampleRate(voiceSampleRate).setChannelMask(channel)
                .setEncoding(encoding).build(),
        )
        .setBufferSizeInBytes(maxOf(requiredBuffer, voiceReadSamples * voiceBytesPerSample * 2))
        .build()

    val samples = ShortArray(voiceSeconds * voiceSampleRate)
    var size = 0
    try {
        if (microphone.state != AudioRecord.STATE_INITIALIZED) {
            throw VoiceInputError("手机麦克风暂时无法录音。")
        }
        microphone.startRecording()
        if (microphone.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            throw VoiceInputError("手机麦克风暂时无法录音。")
        }
        val end = SystemClock.elapsedRealtime() + voiceSeconds * 1000L
        while (!finished.get() && size < samples.size && SystemClock.elapsedRealtime() < end) {
            val read = microphone.read(
                samples, size, minOf(voiceReadSamples, samples.size - size), AudioRecord.READ_NON_BLOCKING,
            )
            if (read < 0) throw VoiceInputError("录音中断，请重新录制。")
            size += read
            if (read == 0) delay(10)
            else currentCoroutineContext().ensureActive()
        }
    } finally {
        if (microphone.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
            runCatching { microphone.stop() }
        }
        microphone.release()
    }
    if (size < minimumVoiceSamples) throw VoiceInputError("录音太短，请重新录制一句话。")
    return samples.copyOf(size)
}

internal fun encodeVoiceWave(samples: ShortArray, sampleCount: Int): ByteArray {
    val byteCount = sampleCount * voiceBytesPerSample
    val buffer = ByteBuffer.allocate(44 + byteCount).order(ByteOrder.LITTLE_ENDIAN)
    buffer.put("RIFF".toByteArray(Charsets.US_ASCII))
    buffer.putInt(36 + byteCount)
    buffer.put("WAVEfmt ".toByteArray(Charsets.US_ASCII))
    buffer.putInt(16)
    buffer.putShort(1) // PCM
    buffer.putShort(1) // Mono
    buffer.putInt(voiceSampleRate)
    buffer.putInt(voiceSampleRate * voiceBytesPerSample)
    buffer.putShort(voiceBytesPerSample.toShort())
    buffer.putShort(16)
    buffer.put("data".toByteArray(Charsets.US_ASCII))
    buffer.putInt(byteCount)
    for (i in 0 until sampleCount) buffer.putShort(samples[i])
    return buffer.array()
}

private val phoneVoiceHttp = OkHttpClient.Builder()
    .connectTimeout(12, TimeUnit.SECONDS)
    .writeTimeout(25, TimeUnit.SECONDS)
    .readTimeout(45, TimeUnit.SECONDS)
    .callTimeout(50, TimeUnit.SECONDS)
    .followRedirects(false)
    .followSslRedirects(false)
    .retryOnConnectionFailure(false)
    .build()

internal fun phoneVoiceTranscriptionRequest(wave: ByteArray, provider: PhoneSpeechProvider, key: String): Request {
    require(PhoneCloudModel.validCredential(key))
    val (endpoint, model) = when (provider) {
        PhoneSpeechProvider.Zhipu ->
            "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions" to "glm-asr-2512"
        PhoneSpeechProvider.SiliconFlow ->
            "https://api.siliconflow.cn/v1/audio/transcriptions" to "FunAudioLLM/SenseVoiceSmall"
    }
    val body = MultipartBody.Builder().setType(MultipartBody.FORM).addFormDataPart("model", model)
    if (provider == PhoneSpeechProvider.Zhipu) body.addFormDataPart("stream", "false")
    body.addFormDataPart("file", "voice.wav", wave.toRequestBody("audio/wav".toMediaType()))
    return Request.Builder()
        .url(endpoint)
        .header("Authorization", "Bearer $key")
        .post(body.build())
        .build()
}

private suspend fun transcribePhoneVoice(wave: ByteArray, provider: PhoneSpeechProvider, key: String): String {
    val request = phoneVoiceTranscriptionRequest(wave, provider, key)
    return suspendCancellableCoroutine { continuation ->
        val call = phoneVoiceHttp.newCall(request)
        continuation.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                continuation.resumeWith(Result.failure(e))
            }

            override fun onResponse(call: Call, response: Response) {
                continuation.resumeWith(runCatching { response.use { parseVoiceResponse(it, provider) } })
            }
        })
    }
}

private fun parseVoiceResponse(response: Response, provider: PhoneSpeechProvider): String {
    val label = provider.label
    if (!response.isSuccessful) {
        throw VoiceInputError(
            when (response.code) {
                401, 403 -> "${label}未接受密钥或语音权限，请在语音设置里核对。"
                402 -> "${label}语音账户余额或配额不足。"
                429 -> "${label}语音请求较多或额度不足，请稍后重试。"
                else -> "${label}语音暂时无法转写，请稍后重试。"
            },
        )
    }
    val source = response.body?.source()
        ?: throw VoiceInputError("${label}语音没有返回转写结果。")
    if (source.request(voiceResponseLimit)) throw VoiceInputError("${label}语音返回的内容过长。")
    val result = JSONObject(source.readUtf8())
    val text = result.opt("text") as? String
    return text?.trim()?.takeIf { it.isNotBlank() }
        ?: throw VoiceInputError("没有识别到清楚的文字，请重新录制。")
}
