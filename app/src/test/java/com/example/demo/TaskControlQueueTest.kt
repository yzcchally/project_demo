package com.example.demo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TaskControlQueueTest {
    @Test fun aPauseResponseCannotDiscardANewerCancel() {
        val queue = TaskControlQueue().apply { select("task-1") }
        assertTrue(queue.enqueue("pause"))
        val pause = queue.begin()!!

        assertTrue(queue.enqueue("cancel"))
        assertNull(queue.begin())
        assertTrue(queue.complete(pause, retryable = false))
        assertEquals("cancel", queue.pending)
        assertEquals("cancel", queue.begin()!!.action)
    }

    @Test fun repeatedRequestsAreCoalescedAndOnlyOneIsInFlight() {
        val queue = TaskControlQueue().apply { select("task-1") }
        assertTrue(queue.enqueue("pause"))
        assertFalse(queue.enqueue("pause"))
        val first = queue.begin()!!
        assertFalse(queue.enqueue("pause"))
        assertNull(queue.begin())
        assertTrue(queue.complete(first, retryable = false))
        assertNull(queue.pending)
        assertNull(queue.begin())
    }

    @Test fun cancelCannotBeReplacedByPauseOrResume() {
        val queue = TaskControlQueue().apply { select("task-1") }
        assertTrue(queue.enqueue("cancel"))
        val cancel = queue.begin()!!
        assertFalse(queue.enqueue("pause"))
        assertFalse(queue.enqueue("resume"))
        assertFalse(queue.enqueue("cancel"))
        assertTrue(queue.complete(cancel, retryable = true))
        assertEquals("cancel", queue.pending)
        assertFalse(queue.enqueue("resume"))
        assertEquals("cancel", queue.begin()!!.action)
    }

    @Test fun aTransientFailureCanRetryWithoutAcceptingTheOldCallback() {
        val queue = TaskControlQueue().apply { select("task-1", "pause") }
        val first = queue.begin()!!
        assertTrue(queue.complete(first, retryable = true))
        val retry = queue.begin()!!
        assertTrue(retry.serial > first.serial)
        assertFalse(queue.complete(first, retryable = false))
        assertEquals(retry, queue.inFlight)
        assertEquals("pause", queue.pending)
        assertTrue(queue.complete(retry, retryable = false))
        assertNull(queue.pending)
    }

    @Test fun aNewerControlReplacesTheFailedControlBeforeRetry() {
        val queue = TaskControlQueue().apply { select("task-1", "pause") }
        val pause = queue.begin()!!
        assertTrue(queue.enqueue("resume"))
        assertTrue(queue.complete(pause, retryable = true))
        assertEquals("resume", queue.pending)
        assertEquals("resume", queue.begin()!!.action)
    }

    @Test fun aPermanentFailureOnlyDiscardsItsOwnControl() {
        val queue = TaskControlQueue().apply { select("task-1", "pause") }
        val pause = queue.begin()!!
        assertTrue(queue.enqueue("resume"))
        assertTrue(queue.complete(pause, retryable = false))
        assertEquals("resume", queue.pending)
        val resume = queue.begin()!!
        assertTrue(queue.complete(resume, retryable = false))
        assertNull(queue.pending)
    }

    @Test fun switchingTasksRejectsCallbacksFromThePreviousTask() {
        val queue = TaskControlQueue().apply { select("task-1", "cancel") }
        val old = queue.begin()!!
        queue.select("task-2", "pause")
        val current = queue.begin()!!
        assertEquals("task-2", queue.taskId)
        assertTrue(current.serial > old.serial)
        assertFalse(queue.complete(old, retryable = false))
        assertEquals(current, queue.inFlight)
        assertEquals("pause", queue.pending)
    }

    @Test fun selectingTheSameTaskStillInvalidatesAnOldRequest() {
        val queue = TaskControlQueue().apply { select("task-1", "pause") }
        val old = queue.begin()!!
        queue.select("task-1", "pause")
        val current = queue.begin()!!
        assertTrue(current.serial > old.serial)
        assertFalse(queue.complete(old, retryable = true))
        assertEquals(current, queue.inFlight)
    }

    @Test fun clearingAFinishedTaskInvalidatesCallbacksAndPendingControls() {
        val queue = TaskControlQueue().apply { select("task-1", "cancel") }
        val old = queue.begin()!!
        queue.clear()
        assertEquals("task-1", queue.taskId)
        assertNull(queue.pending)
        assertNull(queue.inFlight)
        assertNull(queue.begin())
        assertFalse(queue.complete(old, retryable = true))
        assertNull(queue.pending)
    }

    @Test fun onlySupportedControlsForASelectedTaskCanBeRestoredOrQueued() {
        val queue = TaskControlQueue()
        assertFalse(queue.enqueue("cancel"))
        queue.select("task-1", "verify_only")
        assertNull(queue.pending)
        for (action in listOf("verify_only", "retry_prepare", "send", "", "Pause")) {
            assertFalse(queue.enqueue(action))
        }
        assertNull(queue.begin())
        queue.select("", "cancel")
        assertNull(queue.pending)
        assertNull(queue.begin())
    }

    @Test fun aFreshSnapshotConfirmsAPauseWhoseHttpResponseWasLost() {
        val queue = TaskControlQueue().apply { select("task-1", "pause") }
        val pause = queue.begin()!!
        assertTrue(queue.complete(pause, retryable = true))
        assertTrue(queue.reconcileSnapshot("paused"))
        assertNull(queue.pending)
        assertNull(queue.begin())
        assertFalse(queue.complete(pause, retryable = false))
    }

    @Test fun aFreshSnapshotConfirmsAResumeWhoseHttpResponseWasLost() {
        val queue = TaskControlQueue().apply { select("task-1", "resume") }
        val resume = queue.begin()!!
        assertTrue(queue.complete(resume, retryable = true))
        assertTrue(queue.reconcileSnapshot("planning_transit"))
        assertNull(queue.pending)
        assertNull(queue.begin())
    }

    @Test fun aSnapshotCannotResolveAnInFlightRequestOrANewerCancel() {
        val queue = TaskControlQueue().apply { select("task-1", "pause") }
        val pause = queue.begin()!!
        assertFalse(queue.reconcileSnapshot("paused"))
        assertEquals(pause, queue.inFlight)
        assertEquals("pause", queue.pending)
        assertTrue(queue.enqueue("resume"))
        assertFalse(queue.reconcileSnapshot("planning_transit"))
        assertEquals("resume", queue.pending)
        assertTrue(queue.enqueue("cancel"))
        assertTrue(queue.complete(pause, retryable = true))
        for (state in listOf("paused", "planning_transit", "cancelled")) {
            assertFalse(queue.reconcileSnapshot(state))
            assertEquals("cancel", queue.pending)
        }
    }

    @Test fun emptyPausedAndTerminalSnapshotsDoNotConfirmAResume() {
        val queue = TaskControlQueue().apply { select("task-1", "resume") }
        for (state in listOf("", " ", "paused", "completed", "partial", "no_route", "failed", "cancelled", "outcome_unknown", "interrupted")) {
            assertFalse(queue.reconcileSnapshot(state))
            assertEquals("resume", queue.pending)
        }
        queue.select("task-1", "pause")
        assertFalse(queue.reconcileSnapshot("planning_transit"))
        assertEquals("pause", queue.pending)
    }
}
