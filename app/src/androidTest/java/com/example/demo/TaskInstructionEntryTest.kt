package com.example.demo

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextReplacement
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class TaskInstructionEntryTest {
    @get:Rule
    val compose = createComposeRule()

    @Test
    fun largeTextShowsTheComposerAndChoosingAnExampleKeepsItEditable() {
        var submitted: String? = null
        compose.setContent {
            var instruction by remember { mutableStateOf("") }
            val density = LocalDensity.current.density
            CompositionLocalProvider(LocalDensity provides Density(density, fontScale = 1.8f)) {
                Box(Modifier.size(width = 320.dp, height = 540.dp)) {
                    TaskHistoryScaffold(Modifier, rememberScrollState(), onOpen = {}) {
                        Text("小银助手", style = MaterialTheme.typography.headlineMedium)
                        TaskInstructionEntry(
                            instruction = instruction,
                            loading = false,
                            startLabel = "开始任务",
                            onInstructionChange = { instruction = it },
                            onChooseExample = { instruction = it },
                            onStart = { submitted = instruction },
                        )
                    }
                }
            }
        }

        val input = compose.onNode(hasSetTextAction())
        val start = compose.onNodeWithText("开始任务")
        input.assertIsDisplayed()
        start.assertIsDisplayed().assertIsNotEnabled()
        compose.onNodeWithText("扫码打开网页").assertIsDisplayed()
        compose.onNodeWithText("扫码发邮件").assertIsDisplayed()
        compose.onNodeWithText("公交地铁导航").assertIsDisplayed().performClick()

        compose.runOnIdle { assertNull(submitted) }
        input.assertTextContains(DemoCases.navigationExample)
        compose.onNodeWithText("扫码发邮件").assertDoesNotExist()
        start.assertIsEnabled()

        input.performTextReplacement("帮我打开网页。")
        start.performClick()
        compose.runOnIdle { assertEquals("帮我打开网页。", submitted) }

        input.performTextReplacement("")
        start.assertIsNotEnabled()
        compose.onNodeWithText("扫码发邮件").assertExists()
    }
}
