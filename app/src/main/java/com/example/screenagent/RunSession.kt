package com.example.screenagent

import android.accessibilityservice.AccessibilityServiceInfo
import android.content.ComponentName
import android.content.Context
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

object RunSession {
    enum class Phase { Input, Operating, Paused, AwaitingUser }

    var phase by mutableStateOf(Phase.Input)
    var task by mutableStateOf("")
    var notice by mutableStateOf("")
    var userInstruction by mutableStateOf("")

    fun startBlockedReason(context: Context): String? {
        if (ScreenAgentService.instance != null) return null
        if (accessibilityListed(context)) return "无障碍已经打开，但没有连上。请把 Screen Agent 的开关关掉，再打开一次"
        return "开始前需要开启无障碍服务里的 Screen Agent"
    }

    private fun accessibilityListed(context: Context): Boolean {
        val manager = context.getSystemService(AccessibilityManager::class.java)
        val running = manager?.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK).orEmpty()
        if (running.any { it.resolveInfo?.serviceInfo?.name == ScreenAgentService::class.java.name }) return true
        val enabled = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
        ).orEmpty()
        return enabled.split(':').any { entry ->
            val component = ComponentName.unflattenFromString(entry.trim()) ?: return@any false
            component.className == ScreenAgentService::class.java.name ||
                component.className.endsWith(".ScreenAgentService")
        }
    }
}
