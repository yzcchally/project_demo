package com.example.demo

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PhoneBrowserVerificationTest {
    @Test fun requiresTheWholeDisplayedHttpsAddress() {
        val url = "https://example.org/full/path?selected=1#section"
        assertTrue(matchesPhoneBrowserAddress(url, setOf("example.org", url)))
        assertFalse(matchesPhoneBrowserAddress(url, setOf("example.org/full/path?selected=1#section")))
        assertFalse(matchesPhoneBrowserAddress(url, setOf("https://example.org/full/path")))
        assertFalse(matchesPhoneBrowserAddress(url, setOf("https://example.org/full/path?selected=1#other")))
        assertFalse(matchesPhoneBrowserAddress(url, setOf("https://example.org/full/path?selected=1#…")))
        assertFalse(matchesPhoneBrowserAddress("https://example.org/full...", setOf("https://example.org/full...")))
    }

    @Test fun rejectsAmbiguousAndNonpublicAddresses() {
        val url = "https://example.org/"
        assertFalse(matchesPhoneBrowserAddress(url, setOf(url, "https://elsewhere.org/")))
        assertFalse(matchesPhoneBrowserAddress(url, setOf(url, "http://example.org/")))
        assertFalse(matchesPhoneBrowserAddress("https://127.0.0.1/", setOf("https://127.0.0.1/")))
        assertFalse(matchesPhoneBrowserAddress("https://example.org", setOf(url)))
    }
}
