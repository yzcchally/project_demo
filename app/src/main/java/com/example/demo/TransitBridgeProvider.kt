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
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicBoolean

/** Local ADB-only route observation/input, independent of ordered broadcasts. */
class TransitBridgeProvider : ContentProvider() {
    override fun onCreate() = true

    override fun call(method: String, arg: String?, extras: Bundle?): Bundle {
        // call() is a custom provider entry: enforce DUMP explicitly as well as
        // the manifest permission. No URI grants or general app-control API.
        val owner = context ?: return reply(0, "service_unavailable", "")
        owner.enforceCallingPermission(Manifest.permission.DUMP, "DUMP permission required")
        val nonce = arg.orEmpty()
        if (!Regex("[A-Za-z0-9_-]{16,80}").matches(nonce)) return reply(0, "nonce_invalid", "")
        if (method !in setOf("dump_transit_ui", "set_transit_field")) return reply(0, "unsupported_method", nonce)
        val service = MailAccessibilityService.instance ?: return reply(0, "service_unavailable", nonce)
        if (!busy.compareAndSet(false, true)) return reply(0, "bridge_busy", nonce)
        val lease = TransitBridgeLease(SystemClock.elapsedRealtime() + 4500L)
        var submitted = false
        try {
            val future = worker.submit(Callable {
                try {
                    lease.check()
                    if (method == "dump_transit_ui") {
                        val xml = service.dumpTransitUi(nonce, lease)
                        lease.check()
                        if (xml == null) reply(0, service.lastTransitReadReason, nonce)
                        else {
                            val encoded = Base64.encodeToString(xml.toByteArray(Charsets.UTF_8), Base64.URL_SAFE or Base64.NO_WRAP)
                            lease.check()
                            reply(1, "ok", nonce, encoded)
                        }
                    } else {
                        val text = decode(extras?.getString("text"))
                        val expected = decode(extras?.getString("expected_text"))
                        if (text == null || expected == null) reply(0, "input_invalid", nonce)
                        else {
                            val wrote = service.setTransitField(extras?.getString("field").orEmpty(), text,
                                extras?.getString("resource_id").orEmpty(), extras?.getString("bounds").orEmpty(),
                                expected, extras?.getBoolean("use_clipboard", false) ?: false, lease)
                            lease.check()
                            reply(if (wrote) 1 else 0, if (wrote) "ok" else service.lastTransitInputReason, nonce)
                        }
                    }
                } catch (_: TransitBridgeExpired) { reply(0, "bridge_timeout", nonce) }
                catch (_: Exception) { reply(0, "bridge_error", nonce) }
                finally {
                    if (method == "dump_transit_ui") lease.logReadTiming()
                    busy.set(false)
                }
            })
            submitted = true
            return try {
                val result = future.get(6500L, TimeUnit.MILLISECONDS)
                if (lease.active()) result else reply(0, "bridge_timeout", nonce)
            } catch (_: TimeoutException) {
                lease.cancel()
                if (method == "dump_transit_ui") lease.logReadTiming()
                // The native operation may still be blocked. Busy is released
                // only in its own finally; a second caller cannot overlap it.
                reply(0, "bridge_timeout", nonce)
            } catch (_: InterruptedException) {
                lease.cancel(); Thread.currentThread().interrupt()
                reply(0, "bridge_interrupted", nonce)
            } catch (_: ExecutionException) {
                lease.cancel(); reply(0, "bridge_error", nonce)
            }
        } catch (_: RuntimeException) {
            lease.cancel()
            if (!submitted) busy.set(false)
            return reply(0, "bridge_error", nonce)
        }
    }

    private fun reply(code: Int, reason: String, nonce: String, data: String = reason) = Bundle().apply {
        putInt("code", code); putInt("result", code)
        putString("data", data); putString("reason", reason); putString("nonce", nonce)
    }

    private fun decode(value: String?): String? {
        if (value == null || value.length > 4096) return null
        return try { String(Base64.decode(value, Base64.URL_SAFE or Base64.NO_WRAP), Charsets.UTF_8) }
        catch (_: IllegalArgumentException) { null }
    }

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?, selectionArgs: Array<out String>?, sortOrder: String?): Cursor? = null
    override fun getType(uri: Uri): String? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = 0

    companion object {
        private val busy = AtomicBoolean(false)
        private val worker = Executors.newSingleThreadExecutor { runnable ->
            Thread(runnable, "SilverGuideTransitProvider").apply { isDaemon = true }
        }
    }
}
