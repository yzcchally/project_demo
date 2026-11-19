package com.example.demo

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class DemoCasePickerTest {
    @get:Rule
    val compose = createComposeRule()

    @Test
    fun familyDemoShowsTheSampleAndOpensTheLocalFlow() {
        var taskSelections = 0
        var taskCapabilityChecks = 0
        var familyOpens = 0

        compose.setContent {
            DemoCasePickerSheet(
                onDismiss = {},
                onSelect = { taskSelections++ },
                onOpenTaskCases = { taskCapabilityChecks++ },
                onOpenFamilyContact = { familyOpens++ },
                canSelect = true,
                recoverySupported = false,
                recoveryStatus = "",
            )
        }

        compose.onNodeWithText("联系家人").performClick()
        compose.onNodeWithText("5550123", substring = true).assertIsDisplayed()
        compose.onNodeWithText("推荐").assertDoesNotExist()
        compose.onNodeWithText("打开联系家人").performScrollTo().performClick()

        compose.runOnIdle {
            assertEquals(1, familyOpens)
            assertEquals(0, taskCapabilityChecks)
            assertEquals(0, taskSelections)
        }
    }

    @Test
    fun familyDemoRemainsBrowsableDuringAnActiveTask() {
        compose.setContent {
            DemoCasePickerSheet(
                onDismiss = {},
                onSelect = {},
                onOpenTaskCases = {},
                onOpenFamilyContact = {},
                canSelect = false,
                recoverySupported = false,
                recoveryStatus = "",
            )
        }

        compose.onNodeWithText("联系家人").performClick()
        compose.onNodeWithText("5550123", substring = true).assertIsDisplayed()
        compose.onNodeWithText("打开联系家人").assertIsNotEnabled()
    }
}
