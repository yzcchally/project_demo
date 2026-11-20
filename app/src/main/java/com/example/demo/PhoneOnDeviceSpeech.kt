package com.example.demo

import android.content.Context
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineRecognizer
import com.k2fsa.sherpa.onnx.OnlineRecognizerConfig
import com.k2fsa.sherpa.onnx.OnlineZipformer2CtcModelConfig
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

internal object PhoneOnDeviceSpeech {
    private const val sampleRate = 16_000
    private const val modelDirectory = "phone-voice"
    private const val modelName = "model.int8.onnx"
    private const val tokensName = "tokens.txt"
    // Match upstream's streaming wave decoder padding at both ends.
    private const val leadingSilence = sampleRate / 2
    private const val trailingSilence = sampleRate * 8 / 10
    private val decoding = Mutex()
    private val nativeReady by lazy {
        try {
            System.loadLibrary("sherpa-onnx-jni")
            true
        } catch (_: UnsatisfiedLinkError) {
            false
        } catch (_: SecurityException) {
            false
        }
    }

    fun isAvailable(context: Context): Boolean {
        val files = try {
            context.applicationContext.assets.list(modelDirectory).orEmpty()
        } catch (_: IOException) {
            return false
        }
        return modelName in files && tokensName in files && nativeReady
    }

    /** Accepts the actual recorded 16 kHz mono samples and returns the Mandarin transcript. */
    suspend fun transcribe(context: Context, samples: ShortArray): String {
        require(samples.size in sampleRate / 4..sampleRate * 20) { "录音长度不合适，请重新录制一句话。" }
        val app = context.applicationContext
        return withContext(Dispatchers.Default) {
            decoding.withLock {
                check(isAvailable(app)) { "手机上的语音识别暂时不可用。" }
                val coroutine = currentCoroutineContext()
                coroutine.ensureActive()
                val recognizer = OnlineRecognizer(
                    assetManager = app.assets,
                    config = OnlineRecognizerConfig(
                        featConfig = FeatureConfig(sampleRate = sampleRate),
                        modelConfig = OnlineModelConfig(
                            zipformer2Ctc = OnlineZipformer2CtcModelConfig(model = "$modelDirectory/$modelName"),
                            tokens = "$modelDirectory/$tokensName",
                            numThreads = 2,
                        ),
                        enableEndpoint = false,
                    ),
                )
                try {
                    val stream = recognizer.createStream()
                    try {
                        val audio = FloatArray(leadingSilence + samples.size + trailingSilence)
                        for (i in samples.indices) audio[leadingSilence + i] = samples[i] / 32_768.0f
                        coroutine.ensureActive()
                        stream.acceptWaveform(audio, sampleRate)
                        stream.inputFinished()
                        while (recognizer.isReady(stream)) {
                            coroutine.ensureActive()
                            recognizer.decode(stream)
                        }
                        coroutine.ensureActive()
                        recognizer.getResult(stream).text.trim()
                    } finally {
                        stream.release()
                    }
                } finally {
                    recognizer.release()
                }
            }
        }
    }
}
