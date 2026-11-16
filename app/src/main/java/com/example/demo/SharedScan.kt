package com.example.demo

import java.net.URI

internal data class SharedScan(val id: Int, val url: String?)

// This is decided once from a successful, fresh backend snapshot. Never turn
// an unavailable snapshot into permission to fill a form or resume a task.
internal data class ShareBinding(val kind: String, val taskId: String = "", val nonce: String = "")

internal fun preparedScanNonce(taskId: String, preparedTask: String, preparedNonce: String): String =
    if (taskId.isNotBlank() && preparedTask == taskId) preparedNonce else ""

internal fun bindSharedScan(snapshotReady: Boolean, snapshotId: String, state: String,
                            nonce: String, existingTaskId: String, expectedNonce: String = ""): ShareBinding {
    if (!snapshotReady) return ShareBinding("pending")
    if (existingTaskId.isNotEmpty() && expectedNonce.isEmpty()) return ShareBinding("blocked")
    if (snapshotId.isEmpty()) return if (existingTaskId.isEmpty()) ShareBinding("form") else ShareBinding("blocked")
    if (existingTaskId.isNotEmpty() && existingTaskId != snapshotId) return ShareBinding("blocked")
    if (expectedNonce.isNotEmpty() && expectedNonce != nonce) return ShareBinding("blocked")
    if (state in setOf("completed", "partial", "no_route", "failed", "cancelled", "outcome_unknown", "interrupted") && existingTaskId.isEmpty())
        return ShareBinding("form")
    return if (state == "awaiting_qr" && nonce.isNotBlank() && existingTaskId.isNotBlank() && expectedNonce.isNotBlank()) ShareBinding("task", snapshotId, nonce)
    else ShareBinding("blocked")
}

internal fun shareBindingCurrent(binding: ShareBinding, taskId: String, state: String, nonce: String): Boolean =
    binding.kind == "task" && binding.taskId == taskId && state == "awaiting_qr" && binding.nonce == nonce

// Sharing can come from any app. It provides user input, not proof of a scan.
internal fun externalScanUrl(text: String?): String? {
    val value = text?.trim() ?: return null
    if (value.length !in 1..2048 || value.any { it.isWhitespace() || it.isISOControl() }) return null
    val uri = runCatching { URI(value) }.getOrNull() ?: return null
    return value.takeIf {
        uri.scheme.equals("https", ignoreCase = true) && !uri.host.isNullOrBlank() &&
            uri.rawUserInfo == null && uri.port in setOf(-1, 443)
    }
}
