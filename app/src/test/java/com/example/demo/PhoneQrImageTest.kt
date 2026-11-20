package com.example.demo

import com.google.zxing.BarcodeFormat
import com.google.zxing.MultiFormatWriter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PhoneQrImageTest {
    @Test
    fun readsRegularAndInvertedQrButLeavesUrlValidationToCaller() {
        val url = "https://example.org/"
        val ordinary = MultiFormatWriter().encode(url, BarcodeFormat.QR_CODE, 256, 256)
        val pixels = IntArray(ordinary.width * ordinary.height) { index ->
            if (ordinary[index % ordinary.width, index / ordinary.width]) 0 else 0xffffff
        }
        assertEquals(url, decodePhoneQrPixels(ordinary.width, ordinary.height, pixels))
        val inverted = IntArray(pixels.size) { pixels[it] xor 0xffffff }
        assertEquals(url, decodePhoneQrPixels(ordinary.width, ordinary.height, inverted))
    }

    @Test
    fun rejectsOtherBarcodeFormats() {
        val barcode = MultiFormatWriter().encode("1234567890", BarcodeFormat.CODE_128, 384, 128)
        val pixels = IntArray(barcode.width * barcode.height) { index ->
            if (barcode[index % barcode.width, index / barcode.width]) 0 else 0xffffff
        }
        assertNull(decodePhoneQrPixels(barcode.width, barcode.height, pixels))
    }
}
