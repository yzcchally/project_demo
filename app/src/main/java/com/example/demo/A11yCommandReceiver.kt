package com.example.demo

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Base64

class A11yCommandReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val service = MailAccessibilityService.instance
        if (service == null) {
            setResultCode(0)
            return
        }
        when (intent.getStringExtra("op")) {
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
        return String(Base64.decode(encoded, Base64.URL_SAFE or Base64.NO_WRAP), Charsets.UTF_8)
    }
}
