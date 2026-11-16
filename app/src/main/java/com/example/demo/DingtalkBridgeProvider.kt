package com.example.demo

import android.Manifest
import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.util.Base64
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicBoolean

/** ADB-only bounded read of the foreground DingTalk window; no input or send operation. */
class DingtalkBridgeProvider : ContentProvider() {
    override fun onCreate() = true
    override fun call(method: String, arg: String?, extras: Bundle?): Bundle {
        val owner = context ?: return reply(0, "service_unavailable", "")
        owner.enforceCallingPermission(Manifest.permission.DUMP, "DUMP permission required")
        val nonce = arg.orEmpty()
        if (!Regex("[A-Za-z0-9_-]{16,80}").matches(nonce)) return reply(0, "nonce_invalid", "")
        if (method != "dump_dingtalk_ui") return reply(0, "unsupported_method", nonce)
        val service = MailAccessibilityService.instance ?: return reply(0, "service_unavailable", nonce)
        if (!busy.compareAndSet(false, true)) return reply(0, "bridge_busy", nonce)
        val lease = TransitBridgeLease(SystemClock.elapsedRealtime() + 4500L)
        try {
            val future = worker.submit(Callable {
                try {
                    lease.check()
                    val xml = service.dumpDingtalkUi(nonce, lease)
                    lease.check()
                    if (xml == null) reply(0, service.lastDingtalkReadReason, nonce)
                    else reply(1, "ok", nonce, Base64.encodeToString(xml.toByteArray(Charsets.UTF_8), Base64.URL_SAFE or Base64.NO_WRAP))
                } catch (_: TransitBridgeExpired) { reply(0, "bridge_timeout", nonce) }
                  catch (_: Exception) { reply(0, "bridge_error", nonce) }
                finally { busy.set(false) }
            })
            return try {
                val result = future.get(6500L, TimeUnit.MILLISECONDS)
                if (lease.active()) result else reply(0, "bridge_timeout", nonce)
            } catch (_: TimeoutException) { lease.cancel(); reply(0, "bridge_timeout", nonce) }
              catch (_: InterruptedException) { lease.cancel(); Thread.currentThread().interrupt(); reply(0, "bridge_interrupted", nonce) }
              catch (_: Exception) { lease.cancel(); reply(0, "bridge_error", nonce) }
        } catch (_: RuntimeException) { lease.cancel(); busy.set(false); return reply(0, "bridge_error", nonce) }
    }
    private fun reply(code: Int, reason: String, nonce: String, data: String? = null) = Bundle().apply {
        putInt("code", code); putInt("result", code); putString("reason", reason); putString("nonce", nonce); putString("data", data)
    }
    override fun query(uri: Uri, projection: Array<out String>?, selection: String?, selectionArgs: Array<out String>?, sortOrder: String?): Cursor? = null
    override fun getType(uri: Uri): String? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = 0
    companion object {
        private val busy = AtomicBoolean(false)
        private val worker = Executors.newSingleThreadExecutor { runnable -> Thread(runnable, "SilverGuideDingtalkProvider").apply { isDaemon = true } }
    }
}
