package com.example.demo

import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.PersistableBundle
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONObject

private const val WECHAT_PACKAGE = "com.tencent.mm"
private val shareLabelControlCharacters = Regex("[\\p{Cc}\\p{Cf}]")

private fun JSONObject.shareLabel(key: String): String? =
    (opt(key) as? String)?.replace(shareLabelControlCharacters, " ")?.trim()?.takeIf { it.isNotEmpty() }

private fun JSONObject.shareNumber(key: String, max: Int): String? {
    val number = opt(key) as? Number ?: return null
    if (number.toDouble() !in 0.0..max.toDouble()) return null
    return number.toString().toBigDecimalOrNull()?.stripTrailingZeros()?.toPlainString()
}

internal fun transitRouteShareText(task: JSONObject): String? {
    val taskId = task.optString("id")
    val queryId = task.optString("query_id")
    if (taskId.isBlank() || queryId.isBlank() ||
        task.optString("state") != "completed" || task.opt("route_verified") != true
    ) return null
    val route = task.optJSONObject("transit_result") ?: return null
    if (route.optString("task_id") != taskId || route.optString("query_id") != queryId ||
        route.optString("source_app") != "高德地图" || route.optString("classification") != "COMPLETE" ||
        route.opt("route_verified") != true || route.optJSONArray("missing_fields")?.length() != 0
    ) return null

    val segments = route.optJSONArray("segments") ?: return null
    var firstStation: String? = null
    var lastStation: String? = null
    val steps = mutableListOf<String>()
    val missing = mutableListOf<String>()
    for (index in 0 until segments.length()) {
        val segment = segments.optJSONObject(index) ?: return null
        val mode = segment.optString("mode")
        val step = when (mode) {
            "walk" -> buildList {
                add("步行")
                segment.shareNumber("walking_meters", 200_000)?.let { add("约 $it 米") }
                segment.shareNumber("duration_minutes", 1_440)?.let { add("约 $it 分钟") }
                // Walking endpoints and free-text descriptions can name a home or exact location.
                add("具体走法请查看高德地图")
            }.joinToString("，")
            "bus", "subway" -> {
                val board = segment.shareLabel("board") ?: return null
                val alight = segment.shareLabel("alight") ?: return null
                val line = segment.shareLabel("line") ?: return null
                val direction = segment.shareLabel("direction")?.removePrefix("开往")
                    ?.removePrefix("往")?.removeSuffix("方向")?.trim()?.takeIf { it.isNotBlank() }
                    ?: return null
                if (firstStation == null) firstStation = board
                lastStation = alight
                val stops = segment.shareNumber("stops", 300)
                if (stops == null) missing.add("第 ${index + 1} 步站数")
                buildList {
                    add(if (mode == "subway") "乘坐地铁 $line" else "乘坐公交 $line")
                    add("在${board}上车")
                    add("往${direction}方向")
                    stops?.let { add("乘坐 $it 站") }
                    add("在${alight}下车")
                }.joinToString("，")
            }
            else -> return null
        }
        steps.add("${index + 1}. $step")
    }
    if (firstStation == null || lastStation == null) return null

    fun metric(key: String, max: Int, label: String, unit: String): String =
        route.shareNumber(key, max)?.let { "$it $unit" } ?: "未显示".also { missing.add(label) }
    val overview = listOf(
        "预计用时：${metric("duration_minutes", 1_440, "预计用时", "分钟")}",
        "换乘：${metric("transfers", 30, "换乘次数", "次")}",
        "总步行：${metric("walking_meters", 200_000, "总步行距离", "米")}",
        "预计票价：${metric("fare_yuan", 10_000, "预计票价", "元")}",
    )
    return buildString {
        appendLine("高德地图 · 已核对的公交／地铁路线")
        appendLine("查询时间（本机时间）：${deviceQueryTime(route.optString("queried_at"))}")
        appendLine("乘车范围：$firstStation → $lastStation")
        appendLine(overview.joinToString(" · "))
        appendLine()
        steps.forEach { appendLine(it) }
        if (missing.isNotEmpty()) appendLine("未读取：${missing.joinToString("、")}。")
        append("分享仅含乘车站与行程概况；详细起终点、走法和出入口请查看原高德路线。出发前请核对实时信息。")
    }
}

internal fun transitShareIntent(text: String, wechat: Boolean = false): Intent =
    Intent(Intent.ACTION_SEND).apply {
        type = "text/plain"
        putExtra(Intent.EXTRA_TEXT, text)
        if (wechat) setPackage(WECHAT_PACKAGE)
    }

private fun openTransitShare(context: Context, text: String, wechat: Boolean): Boolean {
    val intent = transitShareIntent(text, wechat)
    return try {
        if (wechat && intent.resolveActivity(context.packageManager) == null) return false
        context.startActivity(if (wechat) intent else Intent.createChooser(intent, null))
        true
    } catch (_: ActivityNotFoundException) {
        false
    } catch (_: SecurityException) {
        false
    }
}

private fun copyTransitShare(context: Context, text: String): Boolean {
    val clipboard = context.getSystemService(ClipboardManager::class.java) ?: return false
    val clip = ClipData.newPlainText("高德路线", text)
    clip.description.extras = PersistableBundle().apply {
        putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true)
    }
    return try {
        clipboard.setPrimaryClip(clip)
        true
    } catch (_: SecurityException) {
        false
    }
}

@Composable
internal fun TransitRouteShareButton(text: String, enabled: Boolean, onOpen: () -> Unit) {
    val context = LocalContext.current
    var previewOpen by remember(text) { mutableStateOf(false) }
    OutlinedButton(
        onClick = { onOpen(); previewOpen = true },
        enabled = enabled,
        modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp),
    ) {
        Text("分享路线到微信", fontSize = 20.sp)
    }
    if (previewOpen && enabled) {
        var status by remember(text) { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { previewOpen = false },
            title = { Text("分享路线", fontSize = 23.sp) },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("在微信选择「文件传输助手」，核对后由您点击发送。", fontSize = 19.sp)
                    Button(
                        onClick = {
                            status = if (openTransitShare(context, text, wechat = true)) "" else
                                "微信未安装或无法接收路线文字。请使用系统分享或复制路线。"
                        },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                    ) { Text("打开微信", fontSize = 19.sp) }
                    OutlinedButton(
                        onClick = {
                            status = if (openTransitShare(context, text, wechat = false)) "" else "系统分享不可用，请复制路线文字。"
                        },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                    ) { Text("使用系统分享", fontSize = 19.sp) }
                    TextButton(
                        onClick = {
                            status = if (copyTransitShare(context, text)) "已复制，可粘贴到微信文件传输助手。" else "无法复制，请使用系统分享。"
                        },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                    ) { Text("复制路线文字", fontSize = 19.sp) }
                    if (status.isNotBlank()) {
                        Text(status, fontSize = 17.sp, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                    }
                    Text("分享内容预览", fontSize = 19.sp)
                    Text(text, fontSize = 17.sp)
                }
            },
            confirmButton = {
                TextButton(onClick = { previewOpen = false }) { Text("关闭", fontSize = 19.sp) }
            },
        )
    }
}
