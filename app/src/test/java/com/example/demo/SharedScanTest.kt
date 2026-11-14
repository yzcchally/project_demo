package com.example.demo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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
}
