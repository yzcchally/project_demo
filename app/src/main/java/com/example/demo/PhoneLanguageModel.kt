package com.example.demo

import android.content.Context
import android.os.Build
import android.util.Base64
import java.io.File
import java.io.IOException
import java.net.Proxy
import java.security.SecureRandom
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
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

internal object PhoneLanguageModel {
    private const val EXECUTABLE_NAME = "libsilverguide_llm.so"
    private const val MODEL_NAME = "silverguide-model.gguf"
    private const val LOOPBACK = "127.0.0.1"
    private const val STARTUP_TIMEOUT_MS = 75_000L
    private const val IDLE_TIMEOUT_MS = 90_000L
    private const val MAX_INSTRUCTION_LENGTH = 2_000
    private const val MAX_RESPONSE_BYTES = 32_768L

    private val allowedFields = mapOf(
        "call_family" to listOf("alias"),
        "scan_qr" to emptyList(),
        "open_url" to listOf("url"),
        "plan_transit" to listOf("origin", "destination", "city"),
        "compose_email" to listOf("alias", "url", "recipient", "subject", "body"),
        "share_wechat" to listOf("share_text", "url"),
        "unsupported" to emptyList(),
    )
    private val listeningAddress = Regex("\\blistening on http://127\\.0\\.0\\.1:(\\d{1,5})$")
    private val worker = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val inference = Mutex()
    private val serverLock = Any()
    private val random = SecureRandom()
    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()
    private val http = OkHttpClient.Builder()
        .proxy(Proxy.NO_PROXY)
        .followRedirects(false)
        .followSslRedirects(false)
        .retryOnConnectionFailure(false)
        .connectTimeout(2, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .callTimeout(90, TimeUnit.SECONDS)
        .build()

    private var running: Server? = null
    private var idleCleanup: Job? = null

    private data class ModelVersion(val file: File, val byteCount: Long, val modifiedAt: Long)

    private class Server(val model: ModelVersion, val process: Process, val key: String) {
        val port = CompletableDeferred<Int?>()
    }

    private class ModelError(message: String) : IllegalStateException(message)

    fun modelFile(context: Context): File = File(
        context.getExternalFilesDir("models") ?: File(context.filesDir, "models"),
        MODEL_NAME,
    )

    fun hasRuntime(context: Context): Boolean = executable(context).let { it.isFile && it.canExecute() }

    fun hasModel(context: Context): Boolean = modelFile(context).let { it.isFile && it.canRead() && it.length() > 0 }

    fun close() = synchronized(serverLock) {
        stopLocked()
    }

    suspend fun understand(context: Context, instruction: String): JSONObject = withContext(Dispatchers.IO) {
        val text = instruction.trim()
        if (text.isEmpty()) throw ModelError("请输入要办理的任务。")
        if (text.length > MAX_INSTRUCTION_LENGTH) throw ModelError("任务描述太长，请简短说明后重试。")

        inference.withLock {
            val server = acquireServer(context)
            try {
                val port = withTimeoutOrNull(STARTUP_TIMEOUT_MS) {
                    server.port.await() ?: throw ModelError("本机模型启动失败，请检查模型文件后重试。")
                } ?: throw ModelError("本机模型启动超时，请稍后重试。")
                if (!server.process.isRunning()) throw ModelError("本机模型已经停止，请重试。")
                val result = complete(server, port, text)
                scheduleIdleCleanup(server)
                result
            } catch (error: CancellationException) {
                stop(server)
                throw error
            } catch (error: ModelError) {
                stop(server)
                throw error
            } catch (_: IOException) {
                stop(server)
                throw ModelError("本机模型未能完成理解，请重试。")
            } catch (_: JSONException) {
                stop(server)
                throw ModelError("本机模型没有返回可用的任务，请换一种说法。")
            }
        }
    }

    private fun executable(context: Context) = File(context.applicationInfo.nativeLibraryDir, EXECUTABLE_NAME)

    private fun acquireServer(context: Context): Server = synchronized(serverLock) {
        val binary = executable(context)
        val model = modelFile(context)
        if (!binary.isFile || !binary.canExecute()) {
            stopLocked()
            throw ModelError("此安装包缺少可运行的本机模型组件。")
        }
        val modelVersion = ModelVersion(model, model.length(), model.lastModified())
        if (!model.isFile || !model.canRead() || modelVersion.byteCount == 0L) {
            stopLocked()
            throw ModelError("请先安装本机模型文件。")
        }

        idleCleanup?.cancel()
        idleCleanup = null
        running?.let { if (it.model == modelVersion && it.process.isRunning()) return@synchronized it }
        stopLocked()
        startServer(binary, modelVersion).also { running = it }
    }

    private fun startServer(binary: File, model: ModelVersion): Server {
        val key = Base64.encodeToString(ByteArray(32).also(random::nextBytes), Base64.URL_SAFE or Base64.NO_WRAP)
        val threads = Runtime.getRuntime().availableProcessors().coerceIn(1, 4).toString()
        val builder = ProcessBuilder(
            binary.absolutePath, "--model", model.file.absolutePath,
            "--host", LOOPBACK, "--port", "0", "--api-key", key,
            "--threads", threads, "--threads-http", "2", "--ctx-size", "4096", "--parallel", "1",
            "--n-gpu-layers", "0", "--offline", "--log-colors", "off",
            "--no-webui", "--no-agent", "--no-slots", "--no-cache-prompt",
        ).redirectErrorStream(true)
        builder.environment()["LD_LIBRARY_PATH"] = binary.parentFile!!.absolutePath
        val process = try {
            builder.start()
        } catch (_: IOException) {
            throw ModelError("无法启动本机模型组件。")
        }
        val server = Server(model, process, key)
        worker.launch {
            try {
                // b12556 reports its kernel-assigned port on this private pipe after loading the model.
                // Keep draining the pipe for the life of the child and discard all other output.
                process.inputStream.bufferedReader().useLines { lines ->
                    lines.forEach { line ->
                        if (!server.port.isCompleted) {
                            val port = listeningAddress.find(line)?.groupValues?.get(1)?.toIntOrNull()
                            if (port != null && port in 1..65535) server.port.complete(port)
                        }
                    }
                }
            } catch (_: IOException) {
                // A closed pipe is expected when the owned process is stopped.
            } finally {
                server.port.complete(null)
            }
        }
        return server
    }

    private fun scheduleIdleCleanup(server: Server) = synchronized(serverLock) {
        if (running !== server) throw ModelError("本机模型已经停止，请重试。")
        idleCleanup = worker.launch {
            delay(IDLE_TIMEOUT_MS)
            inference.withLock { stop(server) }
        }
    }

    private fun stop(server: Server) = synchronized(serverLock) {
        if (running === server) stopLocked()
    }

    private fun stopLocked() {
        idleCleanup?.cancel()
        idleCleanup = null
        val server = running ?: return
        running = null
        server.port.complete(null)
        if (Build.VERSION.SDK_INT >= 26) server.process.destroyForcibly() else server.process.destroy()
    }

    private fun Process.isRunning(): Boolean = try {
        exitValue()
        false
    } catch (_: IllegalThreadStateException) {
        true
    }

    private suspend fun complete(server: Server, port: Int, text: String): JSONObject {
        val requestBody = JSONObject()
            .put("messages", JSONArray()
                .put(JSONObject().put("role", "system").put("content", TASK_PROMPT))
                .put(JSONObject().put("role", "user").put("content", text)))
            .put("response_format", JSONObject().put("type", "json_schema").put("schema", resultSchema()))
            .put("chat_template_kwargs", JSONObject().put("enable_thinking", false))
            .put("reasoning_effort", "none")
            .put("max_tokens", 512)
            .put("temperature", 0)
            .put("stream", false)
        val request = Request.Builder()
            .url("http://$LOOPBACK:$port/v1/chat/completions")
            .header("Authorization", "Bearer ${server.key}")
            .post(requestBody.toString().toRequestBody(jsonMediaType))
            .build()
        val response = JSONObject(readResponse(request))
        val choice = response.getJSONArray("choices").getJSONObject(0)
        if (choice.optString("finish_reason") == "length") throw ModelError("本机模型的回复不完整，请简短说明后重试。")
        val parsed = JSONObject(choice.getJSONObject("message").getString("content"))
        val type = parsed.getString("type")
        val fields = allowedFields[type] ?: throw JSONException("Invalid task type")
        return JSONObject().put("type", type).apply {
            for (name in fields) {
                val value = (parsed.opt(name) as? String)?.trim().orEmpty()
                if (value.isNotEmpty()) put(name, value)
            }
            val needsScan = parsed.opt("needs_scan") as? Boolean
            if (type == "compose_email" && needsScan != null) put("needs_scan", needsScan)
        }
    }

    private fun resultSchema(): JSONObject {
        val fields = JSONObject().put("type", JSONObject().put("enum", JSONArray(allowedFields.keys.toList())))
        for (name in allowedFields.values.flatten()) fields.put(name, JSONObject().put("type", "string"))
        fields.put("needs_scan", JSONObject().put("type", "boolean"))
        return JSONObject()
            .put("type", "object")
            .put("properties", fields)
            .put("required", JSONArray().put("type"))
            .put("additionalProperties", false)
    }

    private suspend fun readResponse(request: Request): String = suspendCancellableCoroutine { continuation ->
        val call = http.newCall(request)
        continuation.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                continuation.resumeWithException(e)
            }

            override fun onResponse(call: Call, response: Response) {
                val result = try {
                    response.use {
                        val source = it.body?.source()
                        if (!it.isSuccessful || source == null || source.request(MAX_RESPONSE_BYTES + 1)) {
                            throw IOException("Invalid response from the local model")
                        }
                        source.readUtf8()
                    }
                } catch (error: IOException) {
                    continuation.resumeWithException(error)
                    return
                }
                continuation.resume(result)
            }
        })
    }

    internal const val TASK_PROMPT = """
        你是手机助手的任务解析器。把用户的请求解析为一个 JSON 对象，只写一个 type 和适用字段。
        call_family：要求打电话，alias 填用户说出的联系人称呼。
        scan_qr：只要求扫描二维码。
        open_url：要求打开网页，url 只填用户说出的网址。
        plan_transit：要求查询公交、地铁或公共交通路线，提取 origin、destination、city；'当前位置'保留为起点。
        compose_email：要求写邮件或发邮件，recipient 只填原话里的邮箱，alias 填原话里的联系人称呼，subject 和 body 填用户明确说出的内容。明确要求扫码后用邮件分享网页时 needs_scan 为 true。已有网址可填 url。
        share_wechat：要求通过微信转发。share_text 只填原话中明确要分享的文字，或者把要分享的网址填入 url，不填收件人。
        unsupported：没有上述任务，或无法确定一个任务。
        对口语和很短的需求按含义判断任务。能判断任务时，即使缺少信息也要返回对应 type，缺失字段留空或省略，由应用继续询问。保留人名、地点和网址，不猜联系人地址、号码、网址或地点。不执行任何操作。
        “扫一下二维码” → {"type":"scan_qr"}
        “扫码后把网页发邮件给妈妈” → {"type":"compose_email","alias":"妈妈","needs_scan":true}
        “把 https://example.org 发邮件给妈妈” → {"type":"compose_email","alias":"妈妈","url":"https://example.org"}
        “去大学城南” → {"type":"plan_transit","destination":"大学城南"}
        “打给女儿” → {"type":"call_family","alias":"女儿"}
        “帮我转到微信” → {"type":"share_wechat"}
    """
}
