package com.example.demo

import java.net.URI

internal data class SharedScan(val id: Int, val url: String?)

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
