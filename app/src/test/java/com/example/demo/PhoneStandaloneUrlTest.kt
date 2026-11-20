package com.example.demo

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PhoneStandaloneUrlTest {
    @Test fun onlyPublicHttpsDomainFormsAreAccepted() {
        for (url in listOf("https://example.org", "HTTPS://EXAMPLE.org:443/中文?q=a%20b", "https://xn--fiqs8s.example.org/path")) {
            assertTrue(url, isPhonePublicHttps(url))
        }
        for (url in listOf(
            "http://example.org", "https://localhost", "https://machine.local", "https://host.home.arpa",
            "https://api.intranet", "https://127.0.0.1/", "https://127.1/", "https://0x7f.0.0.0x1/",
            "https://[::1]/", "https://[::ffff:127.0.0.1]/", "https://localhost./", "https://example.org./",
            "https://name:password@example.org", "https://example.org:", "https://example.org:8443",
            "https://example.org\\@127.0.0.1/", "https://example.org\n", "https://example.org/\u202Eevil",
        )) {
            assertFalse(url, isPhonePublicHttps(url))
        }
    }
}
