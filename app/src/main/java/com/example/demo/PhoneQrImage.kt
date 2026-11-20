package com.example.demo

import android.content.ContentResolver
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.ReaderException
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.qrcode.QRCodeReader
import java.io.File
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.runInterruptible
import kotlinx.coroutines.withContext

private const val maxSourceBytes = 24 * 1024 * 1024
private const val maxSourcePixels = 120_000_000L
private const val maxDecodedSide = 2048
private val qrHints = mapOf(DecodeHintType.TRY_HARDER to true)

/** Decodes QR text from one user-selected image. The caller validates the result. */
internal suspend fun decodePhoneQrImage(context: Context, uri: Uri): String? {
    if (uri.scheme != ContentResolver.SCHEME_CONTENT) return null
    val app = context.applicationContext
    return withContext(Dispatchers.IO) {
        val coroutine = currentCoroutineContext()
        val file = try {
            File.createTempFile("phone-qr-", ".image", app.cacheDir)
        } catch (_: IOException) {
            return@withContext null
        }
        try {
            val copied = runInterruptible {
                val input = app.contentResolver.openInputStream(uri) ?: return@runInterruptible false
                input.use { source ->
                    file.outputStream().use { output ->
                        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                        var total = 0
                        while (true) {
                            coroutine.ensureActive()
                            val received = source.read(buffer)
                            if (received < 0) break
                            total += received
                            if (total > maxSourceBytes) return@runInterruptible false
                            output.write(buffer, 0, received)
                        }
                        total > 0
                    }
                }
            }
            if (!copied) return@withContext null
            coroutine.ensureActive()
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(file.path, bounds)
            val width = bounds.outWidth
            val height = bounds.outHeight
            if (width <= 0 || height <= 0 || width.toLong() * height > maxSourcePixels) {
                return@withContext null
            }
            val side = maxOf(width, height)
            var sampleSize = 1
            while ((side + sampleSize - 1) / sampleSize > maxDecodedSide) sampleSize *= 2
            coroutine.ensureActive()
            val bitmap = BitmapFactory.decodeFile(file.path, BitmapFactory.Options().apply {
                inSampleSize = sampleSize
                inPreferredConfig = Bitmap.Config.ARGB_8888
            }) ?: return@withContext null
            try {
                coroutine.ensureActive()
                if (maxOf(bitmap.width, bitmap.height) > maxDecodedSide) return@withContext null
                val pixels = IntArray(bitmap.width * bitmap.height)
                bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
                if (bitmap.hasAlpha()) {
                    // Transparent parts of a typical QR PNG represent a white background.
                    for (index in pixels.indices) {
                        if (index % 65_536 == 0) coroutine.ensureActive()
                        pixels[index] = compositeQrPixelOnWhite(pixels[index])
                    }
                }
                coroutine.ensureActive()
                val text = decodePhoneQrPixels(bitmap.width, bitmap.height, pixels)
                coroutine.ensureActive()
                text
            } finally {
                bitmap.recycle()
            }
        } catch (_: IOException) {
            null
        } catch (_: SecurityException) {
            null
        } catch (_: IllegalArgumentException) {
            null
        } finally {
            file.delete()
        }
    }
}

internal fun decodePhoneQrPixels(width: Int, height: Int, pixels: IntArray): String? {
    val source = RGBLuminanceSource(width, height, pixels)
    val reader = QRCodeReader()
    val result = try {
        reader.decode(BinaryBitmap(HybridBinarizer(source)), qrHints)
    } catch (_: ReaderException) {
        try {
            reader.decode(BinaryBitmap(HybridBinarizer(source.invert())), qrHints)
        } catch (_: ReaderException) {
            return null
        }
    }
    return result.text?.takeIf { it.isNotBlank() }
}

private fun compositeQrPixelOnWhite(pixel: Int): Int {
    val alpha = pixel ushr 24
    if (alpha == 255) return pixel
    fun channel(shift: Int): Int = 255 - ((255 - ((pixel shr shift) and 255)) * alpha / 255)
    return (channel(16) shl 16) or (channel(8) shl 8) or channel(0)
}
