package com.example.demo

import org.junit.Assert.*
import org.junit.Test

class TaskInstructionTest {
    @Test fun explicitChineseSharingRequest() {
        val parsed = parseTaskInstruction("发给 family+test@example.org，主题是社区活动，正文是欢迎参加。")
        assertEquals("family+test@example.org", parsed.recipient)
        assertEquals("社区活动", parsed.subject)
        assertEquals("欢迎参加。", parsed.body)
    }
    @Test fun namedContactRequiresClarification() {
        val parsed = parseTaskInstruction("把这个二维码的网址发给女儿")
        assertEquals("", parsed.recipient)
        assertNull(parsed.url)
    }
    @Test fun preserveFullUrlQueryFromExplicitText() {
        val parsed = parseTaskInstruction("把 https://example.org/event?a=1&b=2 发给 family@example.org")
        assertEquals("https://example.org/event?a=1&b=2", parsed.url)
        assertEquals("网页分享", parsed.subject)
    }
}
