package com.example.screenagent

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import com.journeyapps.barcodescanner.BarcodeCallback
import com.journeyapps.barcodescanner.BarcodeResult
import com.journeyapps.barcodescanner.DecoratedBarcodeView

class QrCaptureActivity : Activity() {
    private var barcode: DecoratedBarcodeView? = null
    private var sent = false
    private var decoding = false
    private val main = Handler(Looper.getMainLooper())
    private val giveUp = Runnable { deliver(null) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val view = DecoratedBarcodeView(this)
        view.setStatusText("将二维码放入框内")
        barcode = view
        setContentView(view)
        main.postDelayed(giveUp, 110_000)
        CaptureBridge.cancel = {
            sent = true
            main.removeCallbacks(giveUp)
            main.post {
                barcode?.pause()
                finish()
            }
        }
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.CAMERA), REQUEST_CAMERA)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQUEST_CAMERA || grantResults.firstOrNull() != PackageManager.PERMISSION_GRANTED) {
            deliver(null)
            return
        }
        startScan()
    }

    override fun onResume() {
        super.onResume()
        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) startScan()
    }

    override fun onPause() {
        barcode?.pause()
        super.onPause()
    }

    private fun startScan() {
        val view = barcode ?: return
        view.resume()
        if (decoding || sent) return
        decoding = true
        view.decodeSingle(object : BarcodeCallback {
            override fun barcodeResult(result: BarcodeResult) {
                deliver(result.text)
            }

            override fun possibleResultPoints(resultPoints: List<com.google.zxing.ResultPoint>) = Unit
        })
    }

    private fun deliver(text: String?) {
        if (sent) return
        sent = true
        main.removeCallbacks(giveUp)
        barcode?.pause()
        CaptureBridge.finish(null, text)
        finish()
    }

    override fun onDestroy() {
        main.removeCallbacks(giveUp)
        barcode?.pause()
        if (CaptureBridge.cancel != null) CaptureBridge.cancel = null
        if (!sent) CaptureBridge.finish(null, null)
        super.onDestroy()
    }

    companion object {
        private const val REQUEST_CAMERA = 12
    }
}
