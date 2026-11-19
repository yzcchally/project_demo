package com.example.demo

import org.json.JSONArray

internal enum class TaskOverlayPhase(val title: String, val canResume: Boolean = false) {
    Paused("任务已暂停", canResume = true),
    WaitingForInput("等待您操作");

    companion object {
        fun fromTaskState(state: String) = when (state) {
            "paused" -> Paused
            "awaiting_qr", "awaiting_confirmation", "awaiting_place_choice", "needs_clarification", "needs_review" -> WaitingForInput
            else -> null
        }
    }
}

internal data class TaskOverlaySnapshot(
    val taskId: String,
    val phase: TaskOverlayPhase,
    val steps: List<String>,
)

/** Only established workflow states can contribute text to the cross-app panel. */
internal fun taskOverlaySteps(events: JSONArray, state: String, transitQuery: String?): List<String> {
    val recent = (0 until events.length()).mapNotNull(events::optJSONObject)
        .filter { transitQuery == null || it.optString("query_id") == transitQuery }
        .sortedBy { it.optLong("seq") }
        .map { it.optString("state") }
    val steps = mutableListOf<String>()
    for (stage in recent + state) {
        val label = when (stage) {
            "understanding" -> "理解任务需求"
            "preparing" -> "准备任务"
            "needs_clarification" -> "等待补充信息"
            "awaiting_qr" -> "等待扫描二维码"
            "opening_webpage" -> "打开并核对网页"
            "preparing_draft" -> "准备并核对邮件草稿"
            "awaiting_confirmation", "needs_review" -> "请回小银核对邮件草稿"
            "sending" -> "发送已确认的邮件"
            "verifying" -> "核对邮件发送记录"
            "planning_transit" -> "查询公交地铁路线"
            "awaiting_place_choice" -> "等待选择地点"
            "reading_transit" -> "读取路线各段"
            "validating_transit" -> "核对路线"
            "viewing_transit" -> "返回高德查看路线"
            "sharing_transit" -> "分享路线"
            "verifying_dingtalk" -> "核对路线分享结果"
            "paused" -> "任务已暂停"
            else -> continue
        }
        if (steps.lastOrNull() != label) steps.add(label)
    }
    return steps.takeLast(6)
}

/** All access is confined to the main thread of the app's process. */
internal object TaskOverlayState {
    var snapshot: TaskOverlaySnapshot? = null
        private set
    private var onControl: ((String) -> Unit)? = null
    private var owner: Any? = null

    fun publish(source: Any, next: TaskOverlaySnapshot?, control: ((String) -> Unit)? = null) {
        if (next == null && owner !== source) return
        owner = if (next == null) null else source
        onControl = control
        if (snapshot == next) return
        snapshot = next
        MailAccessibilityService.instance?.refreshTaskOverlay()
    }

    fun control(expected: TaskOverlaySnapshot, action: String) {
        if (snapshot !== expected) return
        if (action == "cancel" || (action == "resume" && expected.phase.canResume)) onControl?.invoke(action)
    }
}
