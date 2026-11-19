package com.example.screenagent

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.ImageFormat
import android.graphics.SurfaceTexture
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.media.Image
import android.media.ImageReader
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.util.Size
import android.view.Gravity
import android.view.Surface
import android.view.TextureView
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.TextView
import java.io.ByteArrayOutputStream
import kotlin.math.abs
import kotlin.math.max

object CaptureBridge {
    var listener: ((ByteArray?, String?) -> Unit)? = null
    var shoot: (() -> Unit)? = null
    var cancel: (() -> Unit)? = null

    fun finish(bytes: ByteArray?, text: String?) {
        val current = listener ?: return
        listener = null
        shoot = null
        cancel = null
        current(bytes, text)
    }
}

class CaptureActivity : Activity() {
    private var sent = false
    private var opening = false
    private var texture: TextureView? = null
    private var thread: HandlerThread? = null
    private var cameraHandler: Handler? = null
    private var device: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var reader: ImageReader? = null
    private var pendingShot: (() -> Unit)? = null
    private val main = Handler(Looper.getMainLooper())
    private val giveUp = Runnable { deliver(null) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        val preview = TextureView(this)
        texture = preview
        val label = TextView(this).apply {
            text = "对准后，点悬浮窗里的拍照"
            setTextColor(Color.WHITE)
            textSize = 18f
            setPadding(48, 80, 48, 48)
        }
        root.addView(preview, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        root.addView(
            label,
            FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP or Gravity.CENTER_HORIZONTAL),
        )
        setContentView(root)
        val worker = HandlerThread("capture-camera")
        worker.start()
        thread = worker
        cameraHandler = Handler(worker.looper)
        main.postDelayed(giveUp, 110_000)
        CaptureBridge.cancel = {
            sent = true
            main.removeCallbacks(giveUp)
            cameraHandler?.post { closeCamera() }
            main.post { finish() }
        }
        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            preview.surfaceTextureListener = surfaceListener
        } else {
            requestPermissions(arrayOf(Manifest.permission.CAMERA), REQUEST_CAMERA)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQUEST_CAMERA || grantResults.firstOrNull() != PackageManager.PERMISSION_GRANTED) {
            deliver(null)
            return
        }
        val preview = texture ?: return
        preview.surfaceTextureListener = surfaceListener
        if (preview.isAvailable) openCamera()
    }

    private val surfaceListener = object : TextureView.SurfaceTextureListener {
        override fun onSurfaceTextureAvailable(surface: SurfaceTexture, width: Int, height: Int) = openCamera()
        override fun onSurfaceTextureSizeChanged(surface: SurfaceTexture, width: Int, height: Int) = Unit
        override fun onSurfaceTextureDestroyed(surface: SurfaceTexture) = true
        override fun onSurfaceTextureUpdated(surface: SurfaceTexture) = Unit
    }

    private fun openCamera() {
        if (opening || sent) return
        opening = true
        val handler = cameraHandler ?: return
        val manager = getSystemService(CameraManager::class.java)
        val id = manager.cameraIdList.firstOrNull { cameraId ->
            manager.getCameraCharacteristics(cameraId).get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_BACK
        } ?: manager.cameraIdList.firstOrNull()
        if (id == null) {
            deliver(null)
            return
        }
        val characteristics = manager.getCameraCharacteristics(id)
        val map = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
        if (map == null) {
            deliver(null)
            return
        }
        try {
            val size = choose(map.getOutputSizes(ImageFormat.JPEG), 1600)
            reader = ImageReader.newInstance(size.width, size.height, ImageFormat.JPEG, 1)
            reader?.setOnImageAvailableListener({ imageReader ->
                val image = imageReader.acquireLatestImage() ?: return@setOnImageAvailableListener
                val bytes = try {
                    jpegBytes(image)
                } finally {
                    image.close()
                }
                deliver(shrink(bytes))
            }, handler)
            manager.openCamera(id, object : CameraDevice.StateCallback() {
                override fun onOpened(camera: CameraDevice) {
                    device = camera
                    startSession(camera, characteristics)
                }

                override fun onDisconnected(camera: CameraDevice) {
                    camera.close()
                    deliver(null)
                }

                override fun onError(camera: CameraDevice, error: Int) {
                    camera.close()
                    deliver(null)
                }
            }, handler)
        } catch (_: SecurityException) {
            deliver(null)
        } catch (_: RuntimeException) {
            deliver(null)
        }
    }

    private fun startSession(camera: CameraDevice, characteristics: CameraCharacteristics) {
        val preview = texture?.surfaceTexture ?: return
        val imageReader = reader ?: return
        val handler = cameraHandler ?: return
        val previewSize = choose(
            characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)?.getOutputSizes(SurfaceTexture::class.java),
            1280,
        )
        preview.setDefaultBufferSize(previewSize.width, previewSize.height)
        val previewSurface = Surface(preview)
        try {
            camera.createCaptureSession(listOf(previewSurface, imageReader.surface), object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(captureSession: CameraCaptureSession) {
                    session = captureSession
                    try {
                        val previewRequest = camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
                            addTarget(previewSurface)
                            set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
                        }
                        captureSession.setRepeatingRequest(previewRequest.build(), null, handler)
                        val shot = { takePhoto(camera, imageReader, characteristics) }
                        pendingShot = shot
                        CaptureBridge.shoot = shot
                    } catch (_: RuntimeException) {
                        deliver(null)
                    }
                }

                override fun onConfigureFailed(captureSession: CameraCaptureSession) {
                    deliver(null)
                }
            }, handler)
        } catch (_: RuntimeException) {
            deliver(null)
        }
    }

    private fun takePhoto(camera: CameraDevice, imageReader: ImageReader, characteristics: CameraCharacteristics) {
        val captureSession = session ?: return
        val handler = cameraHandler ?: return
        if (sent) return
        try {
            val request = camera.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE).apply {
                addTarget(imageReader.surface)
                set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
                set(CaptureRequest.JPEG_ORIENTATION, jpegOrientation(characteristics))
            }
            captureSession.capture(request.build(), null, handler)
        } catch (_: RuntimeException) {
            deliver(null)
        }
    }

    private fun jpegOrientation(characteristics: CameraCharacteristics): Int {
        val sensor = characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 90
        val deviceRotation = when (display?.rotation) {
            Surface.ROTATION_90 -> 90
            Surface.ROTATION_180 -> 180
            Surface.ROTATION_270 -> 270
            else -> 0
        }
        return (sensor - deviceRotation + 360) % 360
    }

    private fun jpegBytes(image: Image): ByteArray {
        val buffer = image.planes[0].buffer
        val bytes = ByteArray(buffer.remaining())
        buffer.get(bytes)
        return bytes
    }

    private fun shrink(bytes: ByteArray): ByteArray {
        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return bytes
        val longest = max(bitmap.width, bitmap.height)
        val scaled = if (longest <= 1600) bitmap else {
            val ratio = 1600f / longest
            Bitmap.createScaledBitmap(
                bitmap,
                (bitmap.width * ratio).toInt().coerceAtLeast(1),
                (bitmap.height * ratio).toInt().coerceAtLeast(1),
                true,
            )
        }
        val stream = ByteArrayOutputStream()
        scaled.compress(Bitmap.CompressFormat.JPEG, 85, stream)
        if (scaled !== bitmap) scaled.recycle()
        bitmap.recycle()
        return stream.toByteArray()
    }

    private fun choose(sizes: Array<Size>?, target: Int): Size {
        val options = sizes?.toList().orEmpty().ifEmpty { listOf(Size(1280, 720)) }
        return options.minBy { abs(max(it.width, it.height) - target) }
    }

    private fun deliver(bytes: ByteArray?) {
        if (sent) return
        sent = true
        main.removeCallbacks(giveUp)
        pendingShot = null
        CaptureBridge.shoot = null
        cameraHandler?.post { closeCamera() }
        main.post {
            CaptureBridge.finish(bytes, null)
            finish()
        }
    }

    override fun onDestroy() {
        main.removeCallbacks(giveUp)
        closeCamera()
        thread?.quitSafely()
        if (CaptureBridge.cancel != null) CaptureBridge.cancel = null
        if (!sent) CaptureBridge.finish(null, null)
        super.onDestroy()
    }

    private fun closeCamera() {
        try {
            session?.close()
        } catch (_: Exception) {
        }
        try {
            device?.close()
        } catch (_: Exception) {
        }
        try {
            reader?.close()
        } catch (_: Exception) {
        }
        session = null
        device = null
        reader = null
    }

    companion object {
        private const val REQUEST_CAMERA = 11
    }
}
