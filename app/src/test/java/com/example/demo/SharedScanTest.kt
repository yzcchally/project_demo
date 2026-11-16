package com.example.demo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SharedScanTest {
    @Test fun acceptsOneHttpsLinkOnly() {
        assertEquals("https://example.org/path?q=1", externalScanUrl(" https://example.org/path?q=1\n"))
        for (input in listOf(null, "", "http://example.org", "javascript:alert(1)",
            "https://name:password@example.org/", "https://example.org:8080/", "网页 https://example.org/",
            "https://example.org/\nhttps://second.example/", "https://example.org/\u0000",
            "https://example.org/" + "a".repeat(2048))) {
            assertNull(externalScanUrl(input))
        }
    }

    @Test fun coldStartWaitsForSnapshotAndBindsTheCurrentRound() {
        assertEquals("pending", bindSharedScan(false, "", "", "", "saved-task").kind)
        val saved = preparedScanNonce("task-1", "task-1", "nonce-1")
        val binding = bindSharedScan(true, "task-1", "awaiting_qr", "nonce-1", "task-1", saved)
        assertEquals(ShareBinding("task", "task-1", "nonce-1"), binding)
        assertTrue(shareBindingCurrent(binding, "task-1", "awaiting_qr", "nonce-1"))
        // A restored binding remains tied to its original QR round after recreation.
        val restored = ShareBinding(binding.kind, binding.taskId, binding.nonce)
        assertTrue(shareBindingCurrent(restored, "task-1", "awaiting_qr", "nonce-1"))
        assertFalse(shareBindingCurrent(restored, "task-1", "awaiting_qr", "nonce-2"))
        assertFalse(shareBindingCurrent(restored, "task-2", "awaiting_qr", "nonce-1"))
        assertFalse(shareBindingCurrent(restored, "task-1", "paused", "nonce-1"))
    }

    @Test fun missingOrFailedSnapshotCannotSilentlyAuthorizeAnOldTask() {
        assertEquals("pending", bindSharedScan(false, "", "", "", "").kind)
        assertEquals("blocked", bindSharedScan(true, "", "", "", "saved-task").kind)
        assertEquals("blocked", bindSharedScan(true, "task-b", "awaiting_qr", "nonce-b", "task-a", "nonce-a").kind)
        assertEquals("blocked", bindSharedScan(true, "task-a", "awaiting_qr", "nonce-new", "task-a", "nonce-old").kind)
        assertEquals("", preparedScanNonce("task-a", "task-b", "nonce-b"))
        assertEquals("", preparedScanNonce("task-a", "", ""))
        assertEquals("blocked", bindSharedScan(true, "task-a", "awaiting_qr", "nonce-new", "task-a", "").kind)
        assertEquals("blocked", bindSharedScan(true, "task-a", "awaiting_qr", "nonce-a", "", "").kind)
        assertEquals("form", bindSharedScan(true, "", "", "", "").kind)
        assertEquals("blocked", bindSharedScan(true, "task-1", "awaiting_qr", "", "task-1").kind)
        assertEquals("blocked", bindSharedScan(true, "task-1", "completed", "", "task-1").kind)
        assertEquals("form", bindSharedScan(true, "task-1", "completed", "", "").kind)
    }
}
