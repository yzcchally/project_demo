package com.example.demo

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Base64

class A11yCommandReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        setResultCode(0)
        setResultData(null)
        val operation = intent.getStringExtra("op")
        if (operation == "dump_transit_ui" || operation == "set_transit_field") {
            setResultData("content_bridge_required")
            return
        }
        val service = MailAccessibilityService.instance
        if (service == null) {
            setResultCode(0)
            setResultData("service_unavailable")
            return
        }
        when (operation) {
            "set_field" -> {
                val value = decode(intent.getStringExtra("text").orEmpty())
                setResultCode(if (service.setMailField(intent.getStringExtra("field").orEmpty(), value,
                        intent.getBooleanExtra("use_clipboard", false))) 1 else 0)
            }
            "home" -> service.goHome()
            "tap" -> service.tap(
                intent.getIntExtra("x", 0).toFloat(),
                intent.getIntExtra("y", 0).toFloat(),
            )
            "swipe_down" -> service.swipeDown()
            "paste" -> {
                val encoded = intent.getStringExtra("text").orEmpty()
                val value = decode(encoded)
                setResultCode(if (value.isNotEmpty() && service.pasteText(value)) 1 else 0)
            }
        }
    }

    private fun decode(encoded: String): String {
        if (encoded.isBlank()) {
            return ""
        }
        return try { String(Base64.decode(encoded, Base64.URL_SAFE or Base64.NO_WRAP), Charsets.UTF_8) }
        catch (_: IllegalArgumentException) { "" }
    }
}
