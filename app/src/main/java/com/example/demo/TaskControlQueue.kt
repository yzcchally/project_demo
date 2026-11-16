package com.example.demo

/** Holds the latest requested control while allowing only one HTTP request at a time. */
internal class TaskControlQueue {
    data class Attempt(val taskId: String, val action: String, val serial: Long)

    var taskId: String = ""
        private set
    var pending: String? = null
        private set
    var inFlight: Attempt? = null
        private set
    private var serial = 0L

    fun select(taskId: String, restored: String? = null) {
        this.taskId = taskId
        pending = restored?.takeIf { taskId.isNotBlank() && it in allowed }
        inFlight = null
    }

    fun enqueue(action: String): Boolean {
        if (taskId.isBlank() || action !in allowed || pending == action || pending == "cancel") return false
        pending = action
        return true
    }

    fun begin(): Attempt? {
        if (taskId.isBlank() || inFlight != null) return null
        val action = pending ?: return null
        return Attempt(taskId, action, ++serial).also { inFlight = it }
    }

    fun complete(attempt: Attempt, retryable: Boolean): Boolean {
        if (attempt != inFlight) return false
        inFlight = null
        if (!retryable && pending == attempt.action) pending = null
        return true
    }

    /** Call only with a fresh server snapshot after any uncertain request has finished. */
    fun reconcileSnapshot(state: String): Boolean {
        if (inFlight != null) return false
        val satisfied = when (pending) {
            "pause" -> state == "paused"
            "resume" -> state.isNotBlank() && state != "paused" && state !in terminal
            else -> false
        }
        if (satisfied) pending = null
        return satisfied
    }

    fun clear() {
        pending = null
        inFlight = null
    }

    private companion object {
        val allowed = setOf("pause", "resume", "cancel")
        val terminal = setOf("completed", "partial", "no_route", "failed", "cancelled", "outcome_unknown", "interrupted")
    }
}
