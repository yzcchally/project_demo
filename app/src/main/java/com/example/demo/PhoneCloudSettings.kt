package com.example.demo

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import android.util.Base64
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

internal class PhoneCloudStore(context: Context) {
    private val storage = AtomicFile(File(context.noBackupFilesDir, "phone-cloud.v1"))

    suspend fun load(): PhoneCloudConfig? = withContext(Dispatchers.IO) {
        if (!storage.baseFile.exists()) return@withContext null
        val raw = JSONObject(storage.readFully().decodeToString())
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        val iv = Base64.decode(raw.getString("iv"), Base64.NO_WRAP)
        val ciphertext = Base64.decode(raw.getString("ciphertext"), Base64.NO_WRAP)
        cipher.init(Cipher.DECRYPT_MODE, encryptionKey(false), GCMParameterSpec(128, iv))
        val settings = JSONObject(cipher.doFinal(ciphertext).decodeToString())
        val speechProvider = PhoneSpeechProvider.entries.firstOrNull {
            it.name == settings.optString("speech_provider")
        } ?: PhoneSpeechProvider.Zhipu
        PhoneCloudConfig(
            PhoneChatProvider.valueOf(settings.getString("provider")),
            settings.getString("chat"),
            settings.optString("speech"),
            speechProvider,
            settings.optString("siliconflow_speech"),
        )
    }

    suspend fun save(config: PhoneCloudConfig) = withContext(Dispatchers.IO) {
        require(PhoneCloudModel.validCredential(config.chatKey) ||
            (config.chatKey.isEmpty() && PhoneCloudModel.validCredential(config.speechCredential())))
        require(config.speechKey.isEmpty() || PhoneCloudModel.validCredential(config.speechKey))
        require(config.siliconFlowSpeechKey.isEmpty() || PhoneCloudModel.validCredential(config.siliconFlowSpeechKey))
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, encryptionKey(true))
        val plaintext = JSONObject().put("provider", config.chatProvider.name)
            .put("chat", config.chatKey).put("speech", config.speechKey)
            .put("speech_provider", config.speechProvider.name).put("siliconflow_speech", config.siliconFlowSpeechKey)
            .toString().encodeToByteArray()
        val encrypted = try {
            cipher.doFinal(plaintext)
        } finally {
            plaintext.fill(0)
        }
        val payload = JSONObject().put("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .put("ciphertext", Base64.encodeToString(encrypted, Base64.NO_WRAP))
        val stream = storage.startWrite()
        try {
            stream.write(payload.toString().encodeToByteArray())
            storage.finishWrite(stream)
        } catch (error: Exception) {
            storage.failWrite(stream)
            throw error
        }
    }

    private fun encryptionKey(create: Boolean): SecretKey {
        val keystore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keystore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        check(create)
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256).build())
            generateKey()
        }
    }

    private companion object {
        const val KEY_ALIAS = "com.example.demo.phone-cloud.v1"
    }
}

@Composable
internal fun PhoneCloudSettingsDialog(
    current: PhoneCloudConfig?,
    onDismiss: () -> Unit,
    onSaved: (PhoneCloudConfig) -> Unit,
) {
    val app = LocalContext.current.applicationContext
    val scope = rememberCoroutineScope()
    var provider by remember { mutableStateOf(current?.chatProvider ?: PhoneChatProvider.Zhipu) }
    var enteredKey by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf("") }
    val canReuse = current?.chatProvider == provider
    val chatKey = enteredKey.trim().ifEmpty { if (canReuse) current?.chatKey.orEmpty() else "" }
    val speechKey = current?.speechKey.orEmpty()

    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text("在线模型设置") },
        text = {
            Column(Modifier.heightIn(max = 450.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("提交任务后，文字需求会由所选模型处理。")
                for (option in PhoneChatProvider.entries) {
                    OutlinedButton(enabled = !busy, onClick = { provider = option }) {
                        Text(if (provider == option) "${option.label}（已选）" else option.label)
                    }
                }
                OutlinedTextField(value = enteredKey, onValueChange = { enteredKey = it.take(2048) },
                    enabled = !busy, label = { Text("文字模型 API 密钥") }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    supportingText = { if (canReuse && !current?.chatKey.isNullOrBlank()) Text("留空沿用已保存密钥") })
                if (message.isNotEmpty()) Text(message)
            }
        },
        confirmButton = {
            Button(enabled = !busy && PhoneCloudModel.validCredential(chatKey) &&
                (speechKey.isBlank() || PhoneCloudModel.validCredential(speechKey)), onClick = {
                val selected = PhoneCloudConfig(provider, chatKey, speechKey,
                    current?.speechProvider ?: PhoneSpeechProvider.Zhipu, current?.siliconFlowSpeechKey.orEmpty())
                busy = true
                message = "正在从手机连接模型平台…"
                scope.launch {
                    try {
                        val probe = PhoneCloudModel.understand(selected, "帮我打开扫码相机")
                        if (probe.optString("type") != "scan_qr") {
                            throw java.io.IOException("在线模型已返回答复，但扫码测试未正确识别。")
                        }
                        PhoneCloudStore(app).save(selected)
                        onSaved(selected)
                    } catch (error: CancellationException) {
                        throw error
                    } catch (error: Exception) {
                        message = if (error is java.io.IOException) error.message ?: "连接未通过。"
                            else "手机未能保存配置，请解锁手机后重试。"
                    } finally {
                        busy = false
                    }
                }
            }) { Text("连接测试并保存") }
        },
        dismissButton = { TextButton(enabled = !busy, onClick = onDismiss) { Text("关闭") } },
    )
}

@Composable
internal fun PhoneSpeechSettingsDialog(
    current: PhoneCloudConfig?,
    onDismiss: () -> Unit,
    onSaved: (PhoneCloudConfig) -> Unit,
) {
    val app = LocalContext.current.applicationContext
    val scope = rememberCoroutineScope()
    var provider by remember { mutableStateOf(
        current?.speechProvider?.takeIf { current.speechCredential().isNotBlank() } ?: PhoneSpeechProvider.SiliconFlow,
    ) }
    var entered by remember { mutableStateOf("") }
    var verifiedKey by remember { mutableStateOf("") }
    var transcript by remember { mutableStateOf("") }
    var errorText by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val savedKey = current.speechCredential(provider)
    val candidate = entered.trim().ifEmpty { savedKey }
    val draft = PhoneCloudConfig(
        current?.chatProvider ?: PhoneChatProvider.DeepSeek,
        current?.chatKey.orEmpty(),
        if (provider == PhoneSpeechProvider.Zhipu) candidate else current?.speechKey.orEmpty(),
        provider,
        if (provider == PhoneSpeechProvider.SiliconFlow) candidate else current?.siliconFlowSpeechKey.orEmpty(),
    )
    val valid = PhoneCloudModel.validCredential(candidate)

    fun resetVerification() {
        verifiedKey = ""
        transcript = ""
        errorText = ""
    }

    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text("在线语音设置") },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp)) {
                for (option in listOf(PhoneSpeechProvider.SiliconFlow, PhoneSpeechProvider.Zhipu)) {
                    val title = if (option == PhoneSpeechProvider.SiliconFlow) "硅基流动（免费）" else option.label
                    OutlinedButton(enabled = !busy, onClick = {
                        if (provider != option) {
                            provider = option
                            entered = ""
                            resetVerification()
                        }
                    }) { Text(if (provider == option) "$title · 已选" else title) }
                }
                Text(if (provider == PhoneSpeechProvider.SiliconFlow) "实名账号可使用免费的 SenseVoiceSmall 语音模型。"
                    else "智谱语音按开放平台账户额度使用。")
                OutlinedTextField(value = entered, onValueChange = {
                    entered = it.take(2048)
                    resetVerification()
                }, enabled = !busy, label = { Text("${provider.label}语音 API 密钥") }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    supportingText = {
                        if (savedKey.isNotBlank()) Text("留空可重新验证当前密钥")
                        else if (entered.isNotBlank() && !valid) Text("请检查密钥格式")
                    })
                if (valid) {
                    Text("测试时可以说「去体育西」。")
                    key(provider) {
                        PhoneCloudVoiceInput(draft, !busy, onRecognized = { text ->
                            verifiedKey = candidate
                            transcript = text
                        }, idleLabel = "录音测试${provider.label}",
                            successMessage = "${provider.label}已返回文字，请核对测试结果。", showProvider = true)
                    }
                }
                if (transcript.isNotBlank()) Text(transcript)
                if (errorText.isNotBlank()) Text(errorText)
            }
        },
        confirmButton = {
            Button(enabled = valid && verifiedKey == candidate && !busy, onClick = {
                busy = true
                scope.launch {
                    try {
                        PhoneCloudStore(app).save(draft)
                        onSaved(draft)
                    } catch (error: CancellationException) {
                        throw error
                    } catch (_: Exception) {
                        errorText = "手机未能保存密钥，请解锁手机后重试。"
                    } finally {
                        busy = false
                    }
                }
            }) { Text("保存并启用") }
        },
        dismissButton = { TextButton(enabled = !busy, onClick = onDismiss) { Text("关闭") } },
    )
}
