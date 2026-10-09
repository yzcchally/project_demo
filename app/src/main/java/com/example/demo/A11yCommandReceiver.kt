package com.example.demo

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class A11yCommandReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val service = MailAccessibilityService.instance ?: return
        when (intent.getStringExtra("op")) {
            "home" -> service.goHome()
            "tap" -> service.tap(
                intent.getIntExtra("x", 0).toFloat(),
                intent.getIntExtra("y", 0).toFloat(),
            )
            "swipe_down" -> service.swipeDown()
        }
    }
}
