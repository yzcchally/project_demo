package com.example.demo

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Browse examples without replacing an active task or starting an operation. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DemoCasePickerSheet(
    onDismiss: () -> Unit,
    onSelect: (DemoCase) -> Unit,
    onOpenTaskCases: () -> Unit,
    onOpenFamilyContact: () -> Unit,
    canSelect: Boolean,
    recoverySupported: Boolean,
    recoveryStatus: String,
) {
    var domainName by rememberSaveable { mutableStateOf<String?>(null) }
    val domain = DemoDomain.entries.firstOrNull { it.name == domainName }
    var filterName by rememberSaveable { mutableStateOf(DemoFilter.RECOMMENDED.name) }
    val filter = DemoFilter.valueOf(filterName)
    val isFamily = domain == DemoDomain.FAMILY
    val taskDomain = domain?.takeUnless { isFamily }
    val cases = taskDomain?.let { DemoCases.visible(it, filter) }.orEmpty()
    var checkedTaskCapabilities by remember { mutableStateOf(false) }

    LaunchedEffect(taskDomain) {
        if (taskDomain != null && !checkedTaskCapabilities) {
            checkedTaskCapabilities = true
            onOpenTaskCases()
        }
    }

    fun selectDomain(item: DemoDomain) {
        if (domain != item) {
            domainName = item.name
            filterName = DemoFilter.RECOMMENDED.name
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        Column(Modifier.fillMaxWidth().fillMaxHeight(if (domain == null) .5f else .9f)) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 24.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("演示案例", style = MaterialTheme.typography.headlineSmall)
                    if (!isFamily) Text(
                        if (domain == null) "先选择演示类型" else "选好案例，填入后再由您开始",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                TextButton(onClick = onDismiss) { Text("关闭", fontSize = 16.sp) }
            }

            Column(
                Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    listOf(DemoDomain.MAP, DemoDomain.MAIL).forEach { item ->
                        DemoDomainChip(
                            item = item,
                            selected = domain == item,
                            onClick = { selectDomain(item) },
                            modifier = Modifier.weight(1f).heightIn(min = 80.dp),
                        )
                    }
                }
                DemoDomainChip(
                    item = DemoDomain.FAMILY,
                    selected = isFamily,
                    onClick = { selectDomain(DemoDomain.FAMILY) },
                    modifier = Modifier.fillMaxWidth().heightIn(min = 64.dp),
                )
            }

            if (taskDomain != null) Text(
                if (taskDomain == DemoDomain.MAP) "公交地铁路线的完整查询、异常处理和恢复测试。"
                else "扫码、网页与邮件流程，含异常和控制测试。",
                modifier = Modifier.padding(start = 24.dp, end = 24.dp, bottom = 12.dp),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            if (taskDomain != null) Row(
                Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                    .padding(start = 24.dp, end = 24.dp, bottom = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                DemoFilter.entries.forEach { item ->
                    FilterChip(
                        selected = filter == item,
                        onClick = { filterName = item.name },
                        label = { Text(item.label, fontSize = 16.sp) },
                    )
                }
            }

            if (!canSelect) {
                Surface(
                    modifier = Modifier.padding(start = 24.dp, end = 24.dp, bottom = 12.dp),
                    shape = RoundedCornerShape(12.dp),
                    color = MaterialTheme.colorScheme.secondaryContainer,
                ) {
                    Text(
                        if (isFamily) "当前正在处理任务。完成或取消后，再打开联系家人。"
                        else "当前正在处理任务。可先查看案例，完成或取消后再填入。",
                        modifier = Modifier.padding(12.dp),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSecondaryContainer,
                    )
                }
            }

            if (domain != null) key(domain, filter) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                LazyColumn(
                    modifier = Modifier.fillMaxWidth().weight(1f),
                    contentPadding = PaddingValues(start = 20.dp, top = 16.dp, end = 20.dp, bottom = 24.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    if (isFamily) item {
                        FamilyContactDemoChoice(canSelect, onOpenFamilyContact)
                    }
                    items(cases, key = { it.id }) { case ->
                        DemoCaseChoice(
                            case = case,
                            canSelect = canSelect,
                            recoverySupported = recoverySupported,
                            recoveryStatus = recoveryStatus,
                            onSelect = { onSelect(case) },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun DemoDomainChip(
    item: DemoDomain,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier,
) {
    FilterChip(
        selected = selected,
        onClick = onClick,
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
        label = {
            Column(
                Modifier.fillMaxWidth().padding(vertical = 8.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(item.title, fontSize = 20.sp, textAlign = TextAlign.Center)
                Text(item.subtitle, fontSize = 14.sp, textAlign = TextAlign.Center)
            }
        },
    )
}

@Composable
private fun FamilyContactDemoChoice(canSelect: Boolean, onOpen: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(
                "断开网络后，打开联系家人。没有资料时，点击“添加家人”，填入“测试家人”和 5550123 后保存。已有资料时可直接使用。",
                fontSize = 16.sp,
            )
            Text(
                "点击“准备拨号”核对号码，再点“打开拨号页”。看到相同号码后返回，示例号码仅用于核对。",
                fontSize = 16.sp,
            )
            FilledTonalButton(
                onClick = onOpen,
                enabled = canSelect,
                modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
            ) {
                Text("打开联系家人", fontSize = 18.sp)
            }
        }
    }
}

@Composable
private fun DemoCaseChoice(
    case: DemoCase,
    canSelect: Boolean,
    recoverySupported: Boolean,
    recoveryStatus: String,
    onSelect: () -> Unit,
) {
    var expanded by rememberSaveable(case.id) { mutableStateOf(false) }
    val recoveryReady = case.recoveryFault == null || recoverySupported
    val hasSteps = case.category != DemoCategory.SUCCESS && case.steps.isNotEmpty()

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(
                Modifier.fillMaxWidth()
                    .then(if (hasSteps) Modifier
                        .semantics { stateDescription = if (expanded) "步骤已展开" else "步骤已收起" }
                        .clickable(role = Role.Button) { expanded = !expanded } else Modifier),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(
                        case.id,
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.primary,
                    )
                    Text(case.title, style = MaterialTheme.typography.titleMedium, fontSize = 20.sp)
                }
                if (hasSteps) Text(
                    if (expanded) "收起" else "步骤",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary,
                )
            }

            Text(case.summary, style = MaterialTheme.typography.bodyMedium, fontSize = 16.sp)
            if (case.requiresQr) DemoCaseNote("需要现场二维码")
            if (case.recoveryFault != null) DemoCaseNote("启动后会主动切到桌面一次，再自动返回草稿")

            Surface(
                shape = RoundedCornerShape(12.dp),
                color = MaterialTheme.colorScheme.surfaceContainerHighest,
            ) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(
                        "预期结果",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(case.expected, style = MaterialTheme.typography.bodyMedium, fontSize = 16.sp)
                }
            }

            if (hasSteps && expanded) DemoCaseSteps(case.steps)
            if (!recoveryReady) {
                Text(
                    recoveryStatus.ifBlank { "恢复演示暂不可用，请检查电脑后端连接。" },
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
            FilledTonalButton(
                onClick = onSelect,
                enabled = canSelect && recoveryReady,
                modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
                contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
            ) {
                Text(if (case.category == DemoCategory.SUCCESS) "使用完整案例" else "填入此案例", fontSize = 18.sp)
            }
        }
    }
}

/** A compact reminder beside the instruction, with optional step-by-step help. */
@Composable
fun SelectedDemoCaseCard(case: DemoCase, onClear: () -> Unit, canClear: Boolean) {
    var expanded by rememberSaveable(case.id) { mutableStateOf(false) }
    val hasSteps = case.category != DemoCategory.SUCCESS && case.steps.isNotEmpty()
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(
                        "当前演示 · ${case.id}",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSecondaryContainer,
                    )
                    Text(case.title, style = MaterialTheme.typography.titleMedium)
                }
                TextButton(onClick = onClear, enabled = canClear) {
                    Text("取消选择", fontSize = 14.sp)
                }
            }
            Text("预期：${case.expected}", style = MaterialTheme.typography.bodyMedium, fontSize = 15.sp)
            if (hasSteps) TextButton(
                onClick = { expanded = !expanded },
                contentPadding = PaddingValues(horizontal = 0.dp, vertical = 4.dp),
            ) {
                Text(if (expanded) "收起步骤" else "查看操作步骤", fontSize = 15.sp)
            }
            if (hasSteps && expanded) DemoCaseSteps(case.steps)
        }
    }
}

@Composable
private fun DemoCaseNote(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun DemoCaseSteps(steps: List<String>) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        steps.forEachIndexed { index, step ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("${index + 1}.", color = MaterialTheme.colorScheme.primary, fontSize = 16.sp)
                Text(step, modifier = Modifier.weight(1f), fontSize = 16.sp)
            }
        }
    }
}
