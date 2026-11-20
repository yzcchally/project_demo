package com.example.demo

import java.io.IOException
import java.net.Proxy
import java.net.SocketTimeoutException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

internal enum class PhoneChatProvider(val label: String, val url: String, val model: String) {
    DeepSeek("DeepSeek", "https://api.deepseek.com/chat/completions", "deepseek-flash"),
    Zhipu("智谱", "https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-5.3"),
}

internal enum class PhoneSpeechProvider(val label: String) {
    Zhipu("智谱"),
    SiliconFlow("硅基流动"),
}

internal class PhoneCloudConfig(
    val chatProvider: PhoneChatProvider,
    val chatKey: String,
    val speechKey: String,
    val speechProvider: PhoneSpeechProvider = PhoneSpeechProvider.Zhipu,
    val siliconFlowSpeechKey: String = "",
)

internal object PhoneCloudModel {
    private const val MAX_REPLY_BYTES = 65_536L
    private val allowed = setOf("call_family", "scan_qr", "open_url", "plan_transit", "compose_email", "share_wechat", "unsupported")
    private val json = "application/json; charset=utf-8".toMediaType()
    private val client = OkHttpClient.Builder()
        .proxy(Proxy.NO_PROXY)
        .followRedirects(false)
        .followSslRedirects(false)
        .retryOnConnectionFailure(false)
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(38, TimeUnit.SECONDS)
        .callTimeout(45, TimeUnit.SECONDS)
        .build()

    fun validCredential(raw: String) = raw.length in 8..2048 && raw.all { it in '!'..'~' }

    private fun request(config: PhoneCloudConfig, input: String): Request {
        require(validCredential(config.chatKey))
        val body = JSONObject()
            .put("model", config.chatProvider.model)
            .put("response_format", JSONObject().put("type", "json_object"))
            .put("thinking", JSONObject().put("type", "disabled"))
            .put("stream", false)
            .put("temperature", 0.1)
            .put("max_tokens", 768)
            .put("messages", JSONArray()
                .put(JSONObject().put("role", "system").put("content",
                    PhoneLanguageModel.TASK_PROMPT + "\n只返回符合任务格式的 JSON 对象。"))
                .put(JSONObject().put("role", "user").put("content", input)))
        return Request.Builder().url(config.chatProvider.url)
            .header("Authorization", "Bearer ${config.chatKey}")
            .post(body.toString().toRequestBody(json)).build()
    }

    suspend fun understand(config: PhoneCloudConfig, input: String): JSONObject {
        val text = input.trim()
        require(text.length in 2..1500) { "请简短说出想办理的任务。" }
        val reply = readReply(request(config, text))
        try {
            val choice = JSONObject(reply).getJSONArray("choices").getJSONObject(0)
            if (choice.optString("finish_reason") == "length") {
                throw IOException("模型回复未完整返回，请简短说明后重试。")
            }
            val answer = JSONObject(choice.getJSONObject("message").getString("content"))
            if (answer.optString("type") !in allowed) throw JSONException("Unexpected task type")
            return answer
        } catch (_: JSONException) {
            throw IOException("在线模型的答复无法识别，请换一种说法。")
        }
    }

    private suspend fun readReply(request: Request): String = suspendCancellableCoroutine { continuation ->
        val call = client.newCall(request)
        continuation.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (!continuation.isActive) return
                val message = if (e is SocketTimeoutException) "在线模型回应超时，请再试一次。"
                    else "暂时连不上在线模型，请检查手机网络或稍后重试。"
                continuation.resumeWithException(IOException(message))
            }

            override fun onResponse(call: Call, response: Response) {
                val result = runCatching {
                    response.use {
                        if (!it.isSuccessful) throw IOException(when (it.code) {
                            401, 403 -> "模型服务拒绝了密钥，请检查平台和权限。"
                            402 -> "模型账户余额或配额不足。"
                            429 -> "模型服务达到调用限额，请稍后再试。"
                            404 -> "所选模型暂不可用，请检查平台服务。"
                            else -> "在线模型请求失败（HTTP ${it.code}）。"
                        })
                        val source = it.body?.source() ?: throw IOException("模型服务返回了空答复。")
                        if (source.request(MAX_REPLY_BYTES + 1)) throw IOException("模型答复超出可处理大小。")
                        source.readUtf8()
                    }
                }
                if (continuation.isActive) result.fold(continuation::resume, continuation::resumeWithException)
            }
        })
    }
}
