package com.example.demo

import android.os.Build
import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

internal val transitTerminalStates = setOf("completed", "partial", "no_route", "failed", "cancelled", "interrupted", "outcome_unknown")

internal fun transitCompletionReady(state: String, routeVerified: Boolean, classification: String,
                                    routeMatchesQuery: Boolean, dingtalkRequested: Boolean,
                                    dingtalkStatus: String): Boolean =
    state == "completed" && routeVerified && classification == "COMPLETE" && routeMatchesQuery &&
        (!dingtalkRequested || dingtalkStatus == "sent")

@RequiresApi(26)
internal fun transitQueryTime(value: String, zone: ZoneId): String = try {
    DateTimeFormatter.ofPattern("yyyy年M月d日 HH:mm", Locale.SIMPLIFIED_CHINESE)
        .withZone(zone).format(OffsetDateTime.parse(value).toInstant())
} catch (_: Exception) { "待核对" }

internal fun deviceQueryTime(value: String): String =
    if (Build.VERSION.SDK_INT >= 26) transitQueryTime(value, ZoneId.systemDefault()) else "待核对"

internal fun transitClarificationReady(origin: String, destination: String, city: String, missing: Set<String>): Boolean {
    val values = mapOf("origin" to origin, "destination" to destination, "city" to city)
    return (missing + setOf("origin", "destination")).all { values[it]?.isNotBlank() == true }
}

internal fun transitMissingLabel(value: String): String {
    val label = when (value.substringAfterLast('.')) {
        "origin" -> "出发地"; "destination" -> "目的地"; "city" -> "城市"
        "line" -> "线路"; "direction" -> "乘车方向"; "board" -> "上车站"; "alight" -> "下车站"
        "segments" -> "分段行程"; "instructions" -> "步行说明"; "transfers" -> "换乘关系"
        else -> "部分路线详情"
    }
    val segment = Regex("""segments\[(\d+)]""").find(value)?.groupValues?.get(1)?.toIntOrNull()
    return if (segment == null) label else "第${segment + 1}段$label"
}

internal fun transitInstruction(origin: String, destination: String, city: String, preference: String, current: Boolean): String {
    require(destination.isNotBlank()) { "请填写目的地" }
    val cityText = city.trim().takeIf { it.isNotBlank() }?.let { "在${it}，" }.orEmpty()
    val start = origin.trim().takeIf { it.isNotBlank() }?.let { "从${it}去" }
        ?: if (current) "从当前位置去" else "我想去"
    val preferenceText = when (preference) { "fewer_transfers" -> "尽量少换乘"; "less_walking" -> "尽量少步行"; else -> "选择高德推荐方案" }
    return "$cityText$start${destination.trim()}，坐地铁或公交，$preferenceText。"
}

internal data class TransitStep(
    val mode: String, val line: String? = null, val direction: String? = null,
    val board: String? = null, val alight: String? = null, val stops: String? = null,
    val instructions: String? = null, val entrance: String? = null, val exit: String? = null,
) {
    fun text(): String = buildList {
        if (mode == "walk") {
            add("步行")
            board?.takeIf { it.isNotBlank() }?.let { add("从$it") }
            alight?.takeIf { it.isNotBlank() }?.let { add("走到$it") }
            add(instructions?.takeIf { it.isNotBlank() } ?: "详细走法待核对，请查看高德详情")
            if (instructions == "站内外共步行" || Regex("步行\\s*\\d+(?:\\.\\d+)?(?:米|公里)\\s*(?:[·•]\\s*)?\\d+(?:\\.\\d+)?分钟").matches(instructions.orEmpty())) {
                add("具体街道走法未显示，请查看高德地图")
            }
        } else {
            add(if (mode == "subway") "乘坐地铁" else "乘坐公交")
            line?.let { add(it) }
            board?.let { add("在${it}上车") }
            direction?.let {
                val destination = it.trim().removePrefix("开往").removePrefix("往").removeSuffix("方向").trim()
                if (destination.isNotBlank()) add("往${destination}方向")
            }
            stops?.let { add("乘坐${it}站") }
            alight?.let { add("在${it}下车") }
            instructions?.let { add(it) }
        }
        entrance?.let { add("入口：$it") }; exit?.let { add("出口：$it") }
    }.joinToString("，")
}

private fun JSONObject.present(key: String): String? = if (isNull(key)) null else optString(key).trim().takeIf { it.isNotEmpty() && it != "null" }
private fun JSONObject.step(): TransitStep = TransitStep(optString("mode"), present("line"), present("direction"),
    present("board"), present("alight"), present("stops"), present("instructions"), present("entrance"), present("exit"))

/** Explicit public places remain editable; no classroom address becomes a GPS fix. */
@Composable
fun TransitRequestCard(enabled: Boolean, onStart: (String) -> Unit) {
    var origin by rememberSaveable { mutableStateOf("") }
    var destination by rememberSaveable { mutableStateOf("") }
    var city by rememberSaveable { mutableStateOf("") }
    var preference by rememberSaveable { mutableStateOf("fewer_transfers") }
    var current by rememberSaveable { mutableStateOf(true) }
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("公交 / 地铁路线", style = MaterialTheme.typography.titleLarge)
            Text("查询高德中的真实路线，再回这里分步讲解。", fontSize = 18.sp)
            OutlinedTextField(city, { city = it }, label = { Text("城市（不确定可留空）") }, enabled = enabled, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(origin, { origin = it }, label = { Text("出发地（明确站名或地点）") }, enabled = enabled, modifier = Modifier.fillMaxWidth())
            if (origin.isBlank()) Row {
                Checkbox(current, { current = it }, enabled = enabled)
                Text("尝试使用高德已授权的当前位置", fontSize = 18.sp)
            }
            Text("定位无法核实时会请您填写出发地。", fontSize = 16.sp)
            OutlinedTextField(destination, { destination = it }, label = { Text("目的地") }, enabled = enabled, modifier = Modifier.fillMaxWidth())
            Column {
                listOf("fewer_transfers" to "尽量少换乘", "less_walking" to "尽量少步行", "recommended" to "高德推荐").forEach { (key, label) ->
                    Row {
                        RadioButton(preference == key, { preference = key }, enabled = enabled)
                        Text(label, fontSize = 19.sp, modifier = Modifier.padding(top = 12.dp))
                    }
                }
            }
            Button(onClick = { onStart(transitInstruction(origin, destination, city, preference, current)) },
                enabled = enabled && destination.isNotBlank(), modifier = Modifier.fillMaxWidth().heightIn(min = 60.dp)) {
                Text("查询公交地铁路线", fontSize = 21.sp)
            }
        }
    }
}

@Composable
fun TransitTaskCard(task: JSONObject, loading: Boolean, timeline: String,
                    onAction: (String, JSONObject) -> Unit, onSpeak: (String, String) -> Unit, onStop: () -> Unit) {
    val taskId = task.optString("id"); val queryId = task.optString("query_id")
    val state = task.optString("state"); val active = state !in transitTerminalStates
    val route = task.optJSONObject("transit_result")?.takeIf { it.optString("query_id") == queryId && it.optString("task_id") == taskId }
    val serverRequest = task.optJSONObject("transit_request")
    val serverOrigin = serverRequest?.present("origin").orEmpty()
    val serverDestination = serverRequest?.present("destination").orEmpty()
    val serverCity = serverRequest?.present("city").orEmpty()
    val requestedCurrentLocation = serverOrigin.isBlank() && serverRequest?.optBoolean("use_current_location") == true
    var origin by rememberSaveable(taskId, queryId, serverOrigin) { mutableStateOf(serverOrigin) }
    var destination by rememberSaveable(taskId, queryId, serverDestination) { mutableStateOf(serverDestination) }
    var city by rememberSaveable(taskId, queryId, serverCity) { mutableStateOf(serverCity) }
    val missingFields = buildSet {
        for (key in listOf("transit_missing_fields", "clarification_fields")) {
            val fields = task.optJSONArray(key)
            for (index in 0 until (fields?.length() ?: 0)) add(fields!!.optString(index))
        }
    }
    var answer by remember(taskId, queryId) { mutableStateOf("") }
    var asking by remember(taskId, queryId) { mutableStateOf(false) }
    var historyExpanded by rememberSaveable(taskId, queryId) { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    fun payload() = JSONObject().put("query_id", queryId)
    fun act(suffix: String, body: JSONObject = payload()) { onStop(); onAction("/api/tasks/$taskId/transit/$suffix", body) }
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text("公交 / 地铁出行", style = MaterialTheme.typography.titleLarge)
            Text(task.optString("message"), fontSize = 21.sp)
            task.present("transit_recovery_notice")?.let { notice ->
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = MaterialTheme.shapes.medium,
                    color = MaterialTheme.colorScheme.secondaryContainer,
                ) {
                    Text(notice, fontSize = 19.sp, modifier = Modifier.padding(14.dp))
                }
            }
            Text("由电脑经 USB 操作高德地图；本次只查询出发前路线。", fontSize = 16.sp)
            if (route == null) {
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = MaterialTheme.shapes.medium,
                    color = MaterialTheme.colorScheme.surfaceContainerHigh,
                ) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("本次查询", style = MaterialTheme.typography.titleMedium, fontSize = 19.sp)
                        Text("出发地：" + serverOrigin.ifBlank { if (requestedCurrentLocation) "当前位置" else "待补充" }, fontSize = 19.sp)
                        Text("目的地：" + serverDestination.ifBlank { "待补充" }, fontSize = 19.sp)
                        Text("城市：" + serverCity.ifBlank { "待补充或核对" }, fontSize = 16.sp)
                        Text(
                            when {
                                serverOrigin.isNotBlank() -> "起点来源：您在本次需求中提供的地点。"
                                requestedCurrentLocation -> "起点来源：请求使用高德当前位置，定位仍需核对。"
                                else -> "起点来源：等待您补充明确地点。"
                            },
                            fontSize = 16.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            if (state == "needs_clarification") {
                Text("请补充明确地点；“家里”需要具体地址。", fontSize = 19.sp)
                OutlinedTextField(city, { city = it }, label = { Text(if ("city" in missingFields) "城市（必填）" else "城市") }, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(origin, { origin = it }, label = { Text("出发地（必填）") }, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(destination, { destination = it }, label = { Text("目的地（必填）") }, modifier = Modifier.fillMaxWidth())
                Button(onClick = { act("clarify", payload().put("origin", origin).put("destination", destination).put("city", city).put("use_current_location", false)) },
                    enabled = !loading && transitClarificationReady(origin, destination, city, missingFields), modifier = Modifier.fillMaxWidth().heightIn(min = 60.dp)) { Text("补充好了，继续查询", fontSize = 20.sp) }
            }
            if (state == "awaiting_place_choice") task.optJSONObject("place_choice")?.takeIf { it.optString("query_id") == queryId }?.let { choice ->
                Text(if (choice.optString("role") == "origin") "请选择您要出发的地点" else "请选择您要去的地点", fontSize = 21.sp)
                val candidates = choice.optJSONArray("candidates")
                for (index in 0 until (candidates?.length() ?: 0)) {
                    val candidate = candidates!!.getJSONObject(index)
                    OutlinedButton(onClick = { act("place", payload().put("choice_nonce", choice.optString("choice_nonce")).put("candidate_id", candidate.optString("candidate_id"))) },
                        enabled = !loading, modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                            Text(candidate.optString("name"), fontSize = 21.sp)
                            listOfNotNull(candidate.present("city"), candidate.present("address")).joinToString(" · ").takeIf { it.isNotBlank() }?.let { Text(it, fontSize = 17.sp) }
                        }
                    }
                }
            }
            if (route != null) {
                val classification = route.optString("classification")
                val verified = task.optBoolean("route_verified") && route.optBoolean("route_verified") && classification == "COMPLETE"
                Text(when { verified -> "已核对路线"; classification == "PARTIAL" -> "路线只读取了一部分"; classification == "NO_ROUTE" -> "没有查到公交地铁方案"; else -> "本次路线查询未完成" }, fontSize = 24.sp)
                Text("${route.optJSONObject("origin")?.optString("name").orEmpty()} → ${route.optJSONObject("destination")?.optString("name").orEmpty()}", fontSize = 23.sp)
                val overview = buildList {
                    route.present("duration_minutes")?.let { add("预计 $it 分钟") }
                    route.present("transfers")?.let { add("换乘 $it 次") }
                    route.present("walking_meters")?.let { add("步行 $it 米") }
                    route.present("fare_yuan")?.let { add("预计 $it 元") }
                }
                if (overview.isNotEmpty()) Text(overview.joinToString(" · "), fontSize = 20.sp)
                route.present("selection_basis")?.let { Text(it, fontSize = 18.sp) }
                val missing = route.optJSONArray("missing_fields")
                if (classification == "PARTIAL" || !verified) {
                    Text("信息不完整，请先核对高德详情；未读取的信息不会补猜。", fontSize = 19.sp, color = MaterialTheme.colorScheme.error)
                    if ((missing?.length() ?: 0) > 0) Text("缺少：" + (0 until missing!!.length()).map { transitMissingLabel(missing.getString(it)) }.distinct().joinToString("、"), fontSize = 17.sp)
                }
                val segments = route.optJSONArray("segments")
                val steps = (0 until (segments?.length() ?: 0)).map { segments!!.getJSONObject(it).step() }
                steps.forEachIndexed { index, step ->
                    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer)) {
                        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("第 ${index + 1} 步", fontSize = 19.sp)
                            Text(step.text(), fontSize = 23.sp, lineHeight = 34.sp)
                        }
                    }
                }
                Text("来源：${route.optString("source_app", "高德地图")}\n查询时间：${deviceQueryTime(route.optString("queried_at"))}", fontSize = 16.sp)
                val speech = buildString {
                    append(if (!verified) "这份路线信息不完整，请核对高德详情。" else "高德路线。")
                    append(route.optJSONObject("origin")?.optString("name").orEmpty()); append("到"); append(route.optJSONObject("destination")?.optString("name").orEmpty()); append("。")
                    if (overview.isNotEmpty()) append(overview.joinToString("，") + "。")
                    steps.forEachIndexed { index, step -> append("第${index + 1}步，${step.text()}。") }
                }
                Button(onClick = { onSpeak(speech, queryId) }, enabled = !loading && steps.isNotEmpty(), modifier = Modifier.fillMaxWidth().heightIn(min = 60.dp)) { Text("朗读这条路线", fontSize = 21.sp) }
                OutlinedButton(onClick = onStop, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) { Text("停止朗读", fontSize = 20.sp) }
                transitRouteShareText(task)?.let { text ->
                    TransitRouteShareButton(text, enabled = !loading, onOpen = onStop)
                }
                if (task.optString("state") == "outcome_unknown" && task.optString("dingtalk_status") == "unknown"
                    && task.optInt("dingtalk_send_attempts") == 1) {
                    if (task.optString("dingtalk_recipient_digest").isNotBlank() && task.optString("dingtalk_message_digest").isNotBlank()) {
                        Text("点击下方按钮，小银会打开您的钉钉文件助手，只核查本次消息，不会再次发送。", fontSize = 18.sp)
                        OutlinedButton(onClick = { act("verify-dingtalk") }, enabled = !loading && !active,
                            modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) {
                            Text("仅核查钉钉发送结果", fontSize = 20.sp)
                        }
                    } else {
                        Text("本次未记录可核对的会话或消息摘要，请在钉钉本人文件助手手动检查，不要重复发送。", fontSize = 18.sp)
                    }
                }
                OutlinedButton(onClick = { act("show") }, enabled = !loading && !active, modifier = Modifier.fillMaxWidth()) { Text("查看高德详情", fontSize = 20.sp) }
                if (verified) {
                    Text("您还想知道", fontSize = 19.sp)
                    listOf("第一步怎么走？", "我应该在哪一站下车？", "需要换乘几次？").forEach { question ->
                        OutlinedButton(onClick = { scope.launch {
                            asking = true
                            try {
                                val response = request("/api/tasks/$taskId/transit/ask", payload().put("question", question), "POST")
                                if (response.optString("task_id") == taskId && response.optString("query_id") == queryId) answer = response.optString("answer")
                            } catch (e: Exception) { answer = e.message.orEmpty() }
                            finally { asking = false }
                        } }, enabled = !loading && !asking, modifier = Modifier.fillMaxWidth()) { Text(question, fontSize = 19.sp) }
                    }
                    if (answer.isNotBlank()) {
                        Text(answer, fontSize = 22.sp)
                        TextButton(onClick = { onSpeak(answer, queryId) }) { Text("朗读回答", fontSize = 19.sp) }
                    }
                }
            }
            if (state in transitTerminalStates && state != "cancelled") {
                if (route == null) Text("处理上方提示后，可按这些地点重新查询。", fontSize = 18.sp)
                Button(onClick = { act("requery") }, enabled = !loading, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) { Text("重新查询", fontSize = 20.sp) }
                if (route != null) OutlinedButton(onClick = { act("requery", payload().put("preference", "less_walking")) }, enabled = !loading, modifier = Modifier.fillMaxWidth()) { Text("换一个步行更少的方案", fontSize = 19.sp) }
            }
            if (active) {
                OutlinedButton(onClick = { onStop(); onAction("/api/tasks/$taskId/control", JSONObject().put("action", if (state == "paused") "resume" else "pause")) },
                    enabled = !loading, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) { Text(if (state == "paused") "继续任务" else "暂停任务", fontSize = 20.sp) }
                OutlinedButton(onClick = { onStop(); onAction("/api/tasks/$taskId/control", JSONObject().put("action", "cancel")) },
                    enabled = !loading, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) { Text("取消任务", fontSize = 20.sp) }
            }
            if (timeline.isNotBlank()) {
                TextButton(onClick = { historyExpanded = !historyExpanded }) {
                    Text(if (historyExpanded) "收起历史操作记录" else "查看历史操作记录", fontSize = 16.sp)
                }
                if (historyExpanded) {
                    Text("记录可能包含此前查询；当前状态请看页面上方。", fontSize = 16.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(timeline, fontSize = 16.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}
