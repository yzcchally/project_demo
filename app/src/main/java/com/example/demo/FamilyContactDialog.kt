package com.example.demo

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private enum class FamilyContactStep { Show, Edit, ConfirmDial, ConfirmClear }

private data class FamilyContactNotice(val text: String, val error: Boolean = false)

@Composable
internal fun FamilyContactDialog(onDismiss: () -> Unit) {
    val context = LocalContext.current
    val store = remember(context) { FamilyContactStore(context.noBackupFilesDir) }
    val scope = rememberCoroutineScope()
    var contact by remember { mutableStateOf<FamilyContact?>(null) }
    var step by remember { mutableStateOf(FamilyContactStep.Show) }
    var name by remember { mutableStateOf("") }
    var number by remember { mutableStateOf("") }
    var nameError by remember { mutableStateOf(false) }
    var numberError by remember { mutableStateOf(false) }
    var notice by remember { mutableStateOf<FamilyContactNotice?>(null) }
    var busy by remember { mutableStateOf(true) }
    var readError by remember { mutableStateOf(false) }
    val scroll = rememberScrollState()

    suspend fun readContact() {
        busy = true
        try {
            contact = withContext(Dispatchers.IO) { store.read() }
            readError = false
            notice = null
        } catch (_: IOException) {
            contact = null
            readError = true
        } finally {
            busy = false
        }
    }

    LaunchedEffect(store) { readContact() }
    LaunchedEffect(step) { scroll.scrollTo(0) }

    fun back() {
        if (busy) return
        if (step == FamilyContactStep.Show) onDismiss()
        else {
            name = ""
            number = ""
            step = FamilyContactStep.Show
            notice = null
        }
    }

    fun edit() {
        name = contact?.name.orEmpty()
        number = contact?.number.orEmpty()
        nameError = false
        numberError = false
        notice = null
        step = FamilyContactStep.Edit
    }

    fun save() {
        if (busy) return
        val validName = normalizeFamilyContactName(name)
        val validNumber = normalizeFamilyContactNumber(number)
        nameError = validName == null
        numberError = validNumber == null
        if (validName == null || validNumber == null) return
        val updated = FamilyContact(validName, validNumber)
        busy = true
        scope.launch {
            try {
                withContext(Dispatchers.IO) { store.write(updated) }
                contact = updated
                name = ""
                number = ""
                readError = false
                notice = FamilyContactNotice("已保存家人资料。")
                step = FamilyContactStep.Show
            } catch (_: IOException) {
                notice = FamilyContactNotice("这次没能保存，请再试一次。", error = true)
            } finally {
                busy = false
            }
        }
    }

    fun clear() {
        if (busy) return
        busy = true
        scope.launch {
            try {
                withContext(Dispatchers.IO) { store.clear() }
                contact = null
                name = ""
                number = ""
                readError = false
                notice = FamilyContactNotice("已清空家人资料。")
                step = FamilyContactStep.Show
            } catch (_: IOException) {
                notice = FamilyContactNotice("这次没能清空，请再试一次。", error = true)
            } finally {
                busy = false
            }
        }
    }

    fun openDialer() {
        if (busy || step != FamilyContactStep.ConfirmDial) return
        val saved = contact ?: return
        val validNumber = normalizeFamilyContactNumber(saved.number) ?: return
        try {
            context.startActivity(Intent(Intent.ACTION_DIAL, Uri.fromParts("tel", validNumber, null)))
            step = FamilyContactStep.Show
            notice = null
        } catch (_: ActivityNotFoundException) {
            notice = FamilyContactNotice("此手机没有可用的拨号应用。", error = true)
        } catch (_: SecurityException) {
            notice = FamilyContactNotice("手机未能打开拨号页，请用手机的电话应用手动拨号。", error = true)
        }
    }

    Dialog(
        onDismissRequest = ::back,
        properties = DialogProperties(usePlatformDefaultWidth = false, dismissOnClickOutside = false)
    ) {
        Box(modifier = Modifier.fillMaxWidth().padding(16.dp), contentAlignment = Alignment.Center) {
            Surface(modifier = Modifier.widthIn(max = 480.dp).fillMaxWidth(), shape = MaterialTheme.shapes.extraLarge) {
                Column(
                    modifier = Modifier.verticalScroll(scroll).padding(20.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Text(
                        text = when (step) {
                            FamilyContactStep.Show -> "联系家人"
                            FamilyContactStep.Edit -> "填写家人资料"
                            FamilyContactStep.ConfirmDial -> "请核对号码"
                            FamilyContactStep.ConfirmClear -> "清空家人资料？"
                        },
                        style = MaterialTheme.typography.headlineMedium,
                        modifier = Modifier.semantics { heading() }
                    )
                    notice?.let {
                        Text(
                            text = it.text,
                            style = MaterialTheme.typography.bodyLarge,
                            color = if (it.error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
                            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }
                        )
                    }

                    when (step) {
                        FamilyContactStep.Show -> {
                            val saved = contact
                            when {
                                busy -> Text("正在读取家人资料…", style = MaterialTheme.typography.bodyLarge)
                                readError -> {
                                    Text("暂时读不到已保存的资料。可以重新读取、重新填写或清空。", style = MaterialTheme.typography.bodyLarge)
                                    FamilyContactAction("重新读取", primary = true) { scope.launch { readContact() } }
                                    FamilyContactAction("重新填写", onClick = ::edit)
                                    FamilyContactAction("清空家人资料") { step = FamilyContactStep.ConfirmClear }
                                }
                                saved == null -> {
                                    Text("保存一位常联系的家人。资料保存在这部手机，离线也能查看。", style = MaterialTheme.typography.bodyLarge)
                                    FamilyContactAction("添加家人", primary = true, onClick = ::edit)
                                }
                                else -> {
                                    FamilyContactDetails(saved)
                                    Text("可打开手机拨号页，核对后由您拨出。", style = MaterialTheme.typography.bodyLarge)
                                    FamilyContactAction("准备拨号", primary = true) {
                                        notice = null
                                        step = FamilyContactStep.ConfirmDial
                                    }
                                    FamilyContactAction("编辑家人资料", onClick = ::edit)
                                    FamilyContactAction("清空家人资料") {
                                        notice = null
                                        step = FamilyContactStep.ConfirmClear
                                    }
                                }
                            }
                            FamilyContactAction("返回首页", enabled = !busy, onClick = ::back)
                        }
                        FamilyContactStep.Edit -> {
                            OutlinedTextField(
                                value = name,
                                onValueChange = { name = it; nameError = false },
                                modifier = Modifier.fillMaxWidth(),
                                enabled = !busy,
                                textStyle = MaterialTheme.typography.titleLarge,
                                label = { Text("姓名或称呼") },
                                singleLine = true,
                                isError = nameError,
                                supportingText = if (nameError) ({ Text("请填写 1 到 40 个字的姓名或称呼。") }) else null,
                                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next)
                            )
                            OutlinedTextField(
                                value = number,
                                onValueChange = { number = it; numberError = false },
                                modifier = Modifier.fillMaxWidth(),
                                enabled = !busy,
                                textStyle = MaterialTheme.typography.titleLarge,
                                label = { Text("电话号码") },
                                singleLine = true,
                                isError = numberError,
                                supportingText = { Text(if (numberError) "请核对：需要 7 到 15 位数字。" else "可填写区号，也可用 + 加国家代码。") },
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone, imeAction = ImeAction.Done)
                            )
                            FamilyContactAction(if (busy) "请稍候…" else "保存家人资料", primary = true, enabled = !busy, onClick = ::save)
                            FamilyContactAction("返回，不保存", enabled = !busy, onClick = ::back)
                        }
                        FamilyContactStep.ConfirmDial -> {
                            contact?.let { FamilyContactDetails(it) }
                            Text("打开拨号页后，请在手机的电话应用中确认并拨出。", style = MaterialTheme.typography.bodyLarge)
                            FamilyContactAction("打开拨号页", primary = true, enabled = !busy && contact != null, onClick = ::openDialer)
                            FamilyContactAction("取消，返回", enabled = !busy, onClick = ::back)
                        }
                        FamilyContactStep.ConfirmClear -> {
                            Text("清空后，您可以重新添加一位家人。", style = MaterialTheme.typography.bodyLarge)
                            contact?.let { FamilyContactDetails(it) }
                            FamilyContactAction("取消，保留资料", primary = true, enabled = !busy, onClick = ::back)
                            FamilyContactAction(if (busy) "请稍候…" else "确认清空", enabled = !busy, onClick = ::clear)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun FamilyContactDetails(contact: FamilyContact) {
    Text(text = contact.name, style = MaterialTheme.typography.headlineSmall)
    Text(text = contact.number, style = MaterialTheme.typography.headlineMedium)
}

@Composable
private fun FamilyContactAction(
    label: String,
    primary: Boolean = false,
    enabled: Boolean = true,
    onClick: () -> Unit
) {
    val modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)
    val content: @Composable RowScope.() -> Unit = {
        Text(label, style = MaterialTheme.typography.titleMedium)
    }
    if (primary) Button(onClick = onClick, modifier = modifier, enabled = enabled, content = content)
    else OutlinedButton(onClick = onClick, modifier = modifier, enabled = enabled, content = content)
}
