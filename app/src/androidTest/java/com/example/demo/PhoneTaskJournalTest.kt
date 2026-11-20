package com.example.demo

import android.content.ContextWrapper
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class PhoneTaskJournalTest {
    private val appContext = InstrumentationRegistry.getInstrumentation().targetContext

    @get:Rule
    val storage = TemporaryFolder(appContext.cacheDir)

    @get:Rule
    val compose = createComposeRule()

    @Test
    fun reopeningRetainsOriginalTextAndUpdatesFromDifferentInstances() = runBlocking {
        val directory = storage.newFolder()
        val context = object : ContextWrapper(appContext) {
            override fun getNoBackupFilesDir(): File = directory
        }
        val journal = PhoneTaskJournal(context)
        val reopened = PhoneTaskJournal(context)
        val instruction = "  给家人写邮件\n正文是我到了。  "
        val id = journal.begin(instruction)
        listOf(
            async { journal.note(id, "已理解指令") },
            async { reopened.note(id, "请核对收件人") },
        ).awaitAll()
        reopened.note(id, "已打开邮件草稿", PhoneTaskOutcome.HandedOff)
        reopened.note(id, "已打开邮件草稿", PhoneTaskOutcome.HandedOff)

        val saved = PhoneTaskJournal(context).records().single()
        assertEquals(instruction, saved.instruction)
        assertEquals(setOf("已理解指令", "请核对收件人"), saved.steps.dropLast(1).toSet())
        assertEquals("已打开邮件草稿", saved.steps.last())
        assertEquals(3, saved.steps.size)
        assertEquals(PhoneTaskOutcome.HandedOff, saved.outcome)
    }

    @Test
    fun openingDetailsDoesNotRefillUntilRequested() {
        val instruction = "帮我查看到广州塔的公交路线"
        val saved = PhoneTaskRecord(
            id = "history-only",
            instruction = instruction,
            startedAtMillis = 1L,
            steps = listOf("请核对起终点"),
            outcome = PhoneTaskOutcome.Waiting,
        )
        var refilled: String? = null
        compose.setContent {
            PhoneTaskJournalDialog(listOf(saved), onDismiss = {}, onReuseInstruction = { refilled = it })
        }

        compose.onNodeWithText(instruction).performClick()
        compose.onNodeWithText("请核对起终点").assertIsDisplayed()
        compose.runOnIdle { assertNull(refilled) }
        compose.onNodeWithText("填回原指令").performClick()
        compose.runOnIdle { assertEquals(instruction, refilled) }
    }
}
