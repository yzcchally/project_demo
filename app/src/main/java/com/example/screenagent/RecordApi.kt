package com.example.screenagent

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

object RecordApi {
    private val client = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()
    private val json = "application/json; charset=utf-8".toMediaType()

    class Item(val id: String, val name: String)
    class Detail(
        val id: String,
        val name: String,
        val lastStep: Int,
        val dialogues: List<RunSession.Dialogue>,
    )

    fun create(base: String): Item {
        val request = Request.Builder()
            .url(root(base) + "/records")
            .post("{}".toRequestBody(json))
            .build()
        client.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) error("建不了记录")
            val data = JSONObject(body)
            return Item(data.optString("id"), data.optString("name").ifBlank { "未命名" })
        }
    }

    fun list(base: String): List<Item> {
        val request = Request.Builder().url(root(base) + "/records").build()
        client.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) error("读不到记录")
            val rows = JSONObject(body).optJSONArray("records") ?: JSONArray()
            return (0 until rows.length()).map { index ->
                val row = rows.getJSONObject(index)
                Item(row.optString("id"), row.optString("name").ifBlank { "未命名" })
            }
        }
    }

    fun detail(base: String, id: String): Detail {
        val request = Request.Builder().url(root(base) + "/records/" + id).build()
        client.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) error("打不开记录")
            val data = JSONObject(body)
            val rows = data.optJSONArray("dialogues") ?: JSONArray()
            val dialogues = (0 until rows.length()).map { index ->
                val row = rows.getJSONObject(index)
                RunSession.Dialogue(
                    row.optInt("index", index + 1),
                    row.optString("prompt"),
                    prompts(row.optJSONArray("additions")),
                    steps(row.optJSONArray("progress")),
                )
            }
            return Detail(
                data.optString("id", id),
                data.optString("name").ifBlank { "未命名" },
                data.optInt("last_step"),
                dialogues,
            )
        }
    }

    fun delete(base: String, id: String) {
        val request = Request.Builder().url(root(base) + "/records/" + id).delete().build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) error("删不掉记录")
        }
    }

    private fun prompts(rows: JSONArray?): List<RunSession.AddedPrompt> {
        if (rows == null) return emptyList()
        return (0 until rows.length()).mapNotNull { index ->
            val row = rows.optJSONObject(index) ?: return@mapNotNull null
            val text = row.optString("text")
            if (text.isBlank()) null else RunSession.AddedPrompt(row.optInt("progress"), text)
        }
    }

    private fun steps(rows: JSONArray?): List<RunSession.ProgressItem> {
        if (rows == null) return emptyList()
        return (0 until rows.length()).mapNotNull { index ->
            val row = rows.optJSONObject(index) ?: return@mapNotNull null
            val text = row.optString("text")
            if (text.isBlank()) null else RunSession.ProgressItem(
                text,
                if (row.has("step") && !row.isNull("step")) row.optInt("step") else null,
            )
        }
    }

    private fun root(base: String): String {
        val trimmed = base.trim().trimEnd('/')
        return if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) trimmed else "http://$trimmed"
    }
}
