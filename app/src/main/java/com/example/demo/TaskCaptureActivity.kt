package com.example.demo

import android.content.SharedPreferences
import com.journeyapps.barcodescanner.CaptureActivity
import org.json.JSONObject

class TaskCaptureActivity : CaptureActivity() {
    private val prefs by lazy { getSharedPreferences("silverguide", 0) }
    private val listener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
        if (key == "live_task") checkTask()
    }
    private fun checkTask() {
        val owner = prefs.getString("active_scan_task", "").orEmpty()
        if (owner.isBlank()) return
        val task = try { JSONObject(prefs.getString("live_task", "{}").orEmpty()) } catch (_: Exception) { return }
        if (task.optString("id") == owner && task.optString("state") in setOf("paused", "cancelled", "failed", "interrupted")) finish()
    }
    override fun onResume() { super.onResume(); prefs.registerOnSharedPreferenceChangeListener(listener); checkTask() }
    override fun onPause() { prefs.unregisterOnSharedPreferenceChangeListener(listener); super.onPause() }
}
