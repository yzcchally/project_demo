package com.example.demo

import android.content.Context
import android.net.Uri
import android.system.Os
import java.io.File
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext

internal object PhoneModelInstall {
    const val sizeLabel = "约 1.1 GB"
    private const val size = 1_117_320_736L
    private const val digest = "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"
    private const val downloadUrl =
        "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/" +
            "91cad51170dc346986eccefdc2dd33a9da36ead9/qwen2.5-1.5b-instruct-q4_k_m.gguf"

    suspend fun install(context: Context, document: Uri?, onProgress: (Int) -> Unit) = withContext(Dispatchers.IO) {
        val target = PhoneLanguageModel.modelFile(context)
        val directory = checkNotNull(target.parentFile)
        if ((!directory.isDirectory && !directory.mkdirs()) || directory.usableSpace < size) {
            error("手机存储空间不足，请先腾出 $sizeLabel 空间。")
        }
        val pending = File(directory, ".model-${UUID.randomUUID()}.part")
        try {
            val connection = if (document == null) {
                (URL(downloadUrl).openConnection() as HttpURLConnection).apply {
                    connectTimeout = 15_000
                    readTimeout = 30_000
                }
            } else null
            try {
                val stream = if (document != null) {
                    context.contentResolver.openInputStream(document) ?: error("无法读取所选文件。")
                } else {
                    val remote = checkNotNull(connection)
                    if (remote.responseCode != 200 || remote.url.protocol != "https") error("模型下载暂不可用。")
                    remote.inputStream
                }
                stream.use { writeModel(it, pending, onProgress) }
            } finally {
                connection?.disconnect()
            }
            ensureActive()
            PhoneLanguageModel.close()
            Os.rename(pending.absolutePath, target.absolutePath)
        } finally {
            pending.delete()
        }
    }

    private suspend fun writeModel(source: InputStream, file: File, onProgress: (Int) -> Unit) =
        withContext(Dispatchers.IO) {
            val hash = MessageDigest.getInstance("SHA-256")
            val buffer = ByteArray(256 * 1024)
            var bytes = 0L
            var previous = -1
            file.outputStream().buffered().use { output ->
                while (true) {
                    ensureActive()
                    val count = source.read(buffer)
                    if (count < 0) break
                    bytes += count
                    require(bytes <= size) { "请选择小银支持的 Qwen2.5 1.5B 手机模型。" }
                    hash.update(buffer, 0, count)
                    output.write(buffer, 0, count)
                    val percent = (bytes * 100 / size).toInt()
                    if (percent != previous) {
                        previous = percent
                        withContext(Dispatchers.Main) { onProgress(percent) }
                    }
                }
            }
            val checksum = hash.digest().joinToString("") { "%02x".format(it) }
            check(bytes == size && checksum == digest) { "模型校验失败，请重新下载或选择正确的文件。" }
        }
}
