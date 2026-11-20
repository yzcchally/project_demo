package com.example.demo

import java.nio.ByteBuffer
import java.nio.ByteOrder
import okhttp3.MultipartBody
import okio.Buffer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class PhoneCloudVoiceWaveTest {
    @Test
    fun selectsOnlyTheCredentialForTheRequestedSpeechProvider() {
        val config = PhoneCloudConfig(
            chatProvider = PhoneChatProvider.Zhipu,
            chatKey = "test-zhipu-chat",
            speechKey = "test-zhipu-speech",
            speechProvider = PhoneSpeechProvider.SiliconFlow,
            siliconFlowSpeechKey = "test-siliconflow-speech",
        )
        assertEquals("test-siliconflow-speech", config.speechCredential())
        assertEquals("test-zhipu-speech", config.speechCredential(PhoneSpeechProvider.Zhipu))

        val chatOnly = PhoneCloudConfig(PhoneChatProvider.Zhipu, "test-zhipu-chat", "")
        assertEquals("test-zhipu-chat", chatOnly.speechCredential())
        assertEquals("", chatOnly.speechCredential(PhoneSpeechProvider.SiliconFlow))
        assertEquals("", PhoneCloudConfig(PhoneChatProvider.DeepSeek, "test-deepseek-chat", "").speechCredential())
        assertEquals("", (null as PhoneCloudConfig?).speechCredential())
    }

    @Test
    fun buildsTheProviderSpecificMultipartRequests() {
        val wave = encodeVoiceWave(shortArrayOf(1, -2), 2)
        val zhipu = phoneVoiceTranscriptionRequest(wave, PhoneSpeechProvider.Zhipu, "test-zhipu-speech")
        val siliconFlow = phoneVoiceTranscriptionRequest(wave, PhoneSpeechProvider.SiliconFlow, "test-siliconflow-speech")
        assertEquals("https://open.bigmodel.cn/api/paas/v4/audio/transcriptions", zhipu.url.toString())
        assertEquals("https://api.siliconflow.cn/v1/audio/transcriptions", siliconFlow.url.toString())
        assertEquals("Bearer test-zhipu-speech", zhipu.header("Authorization"))
        assertEquals("Bearer test-siliconflow-speech", siliconFlow.header("Authorization"))

        for ((request, fields) in listOf(
            zhipu to mapOf("model" to "glm-asr-2512", "stream" to "false"),
            siliconFlow to mapOf("model" to "FunAudioLLM/SenseVoiceSmall"),
        )) {
            assertEquals("POST", request.method)
            val body = request.body as MultipartBody
            assertEquals("multipart/form-data", body.type.toString())
            assertEquals(fields.keys.map { "form-data; name=\"$it\"" }.toSet() +
                "form-data; name=\"file\"; filename=\"voice.wav\"", body.parts.map { it.headers?.get("Content-Disposition") }.toSet())
            for ((name, expected) in fields) {
                val part = body.parts.single { it.headers?.get("Content-Disposition") == "form-data; name=\"$name\"" }
                assertEquals(expected, Buffer().also { part.body.writeTo(it) }.readUtf8())
            }
            val file = body.parts.single { it.body.contentType()?.toString() == "audio/wav" }
            assertArrayEquals(wave, Buffer().also { file.body.writeTo(it) }.readByteArray())
        }
    }
    @Test
    fun encodesTheRecordedSamplesAsLittleEndianMonoPcm() {
        val samples = shortArrayOf(0x1234, -2, 600)
        val bytes = encodeVoiceWave(samples, 2)
        val wave = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)

        assertEquals(48, bytes.size)
        assertEquals("RIFF", String(bytes, 0, 4, Charsets.US_ASCII))
        assertEquals(40, wave.getInt(4))
        assertEquals("WAVEfmt ", String(bytes, 8, 8, Charsets.US_ASCII))
        assertEquals(16, wave.getInt(16))
        assertEquals(1, wave.getShort(20).toInt())
        assertEquals(1, wave.getShort(22).toInt())
        assertEquals(16_000, wave.getInt(24))
        assertEquals(32_000, wave.getInt(28))
        assertEquals(2, wave.getShort(32).toInt())
        assertEquals(16, wave.getShort(34).toInt())
        assertEquals("data", String(bytes, 36, 4, Charsets.US_ASCII))
        assertEquals(4, wave.getInt(40))
        assertEquals(0x1234, wave.getShort(44).toInt())
        assertEquals(-2, wave.getShort(46).toInt())
    }
}
