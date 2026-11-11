package com.example.demo

import org.junit.Assert.*
import org.junit.Test

class DemoCasesTest {
    @Test fun visibleCasesHaveUniqueStableIdsWithoutTheOldWebOnlyCase() {
        assertEquals((2..12).map { "T%02d".format(it) }, DemoCases.all.map { it.id })
        assertEquals(11, DemoCases.all.map { it.id }.toSet().size)
        DemoCases.all.forEach { assertEquals(it, DemoCases.find(it.id)) }
        assertNull(DemoCases.find("T01"))
        assertNull(DemoCases.find("not-a-case"))
    }

    @Test fun theSingleNormalDemoIsContinuousAndOtherCasesKeepTheirSteps() {
        val normalCases = DemoCases.all.filter { it.category == DemoCategory.SUCCESS }
        assertEquals(listOf("T02"), normalCases.map { it.id })
        assertEquals("完整扫码发信演示", normalCases.single().title)
        assertTrue(normalCases.single().steps.isEmpty())
        assertEquals(DemoCategory.CHECKS, DemoCases.find("T03")!!.category)
        DemoCases.all.forEach {
            assertTrue(it.title.isNotBlank())
            assertTrue(it.summary.isNotBlank())
            assertTrue(it.promptTemplate.isNotBlank())
            if (it.category != DemoCategory.SUCCESS) assertTrue(it.steps.size in 3..5)
            assertTrue(it.steps.all(String::isNotBlank))
            assertTrue(it.expected.isNotBlank())
        }
    }

    @Test fun mailCaseSubjectsReceiveFreshRunLabels() {
        val mailCases = DemoCases.all.filter { "主题是" in it.promptTemplate }
        assertEquals(setOf("T02", "T03", "T04", "T05", "T12"), mailCases.map { it.id }.toSet())
        mailCases.forEach {
            val first = DemoCases.promptFor(it, "RUN-A")
            val second = DemoCases.promptFor(it, "RUN-B")
            assertNotEquals(first, second)
            assertTrue(first.contains("${it.id}-RUN-A"))
            assertTrue(second.contains("${it.id}-RUN-B"))
            assertFalse(first.contains("{run}"))
            assertFalse(second.contains("{run}"))
        }
    }

    @Test fun promptsRetainUserApprovedContactAliasAndExampleAddressesOnly() {
        val email = Regex("[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}")
        DemoCases.all.forEach { example ->
            email.findAll(example.promptTemplate).forEach {
                assertTrue(it.value.endsWith("@example.org"))
            }
        }
        listOf("T02", "T03", "T12").forEach {
            assertTrue(DemoCases.find(it)!!.promptTemplate.contains("测试收件人"))
        }
        assertFalse(DemoCases.find("T04")!!.promptTemplate.contains("测试收件人"))
        assertEquals(2, email.findAll(DemoCases.find("T05")!!.promptTemplate).count())
    }

    @Test fun onlyTheExplicitRecoveryCaseRequestsFaultInjection() {
        val faultCases = DemoCases.all.filter { it.recoveryFault != null }
        assertEquals(listOf("T12"), faultCases.map { it.id })
        assertEquals("composer-background", faultCases.single().recoveryFault)
        assertEquals(DemoCategory.RECOVERY, faultCases.single().category)
        assertTrue(faultCases.single().steps.any { "桌面一次" in it })
    }

    @Test fun paymentCaseIsARejectionExampleWithNoRecoveryRequest() {
        val example = DemoCases.find("T08")!!
        assertEquals("帮我给张三转账一百元。", example.promptTemplate)
        assertEquals(DemoCategory.CHECKS, example.category)
        assertTrue(example.expected.contains("拒绝"))
        assertTrue(example.expected.contains("不打开支付应用"))
        assertNull(example.recoveryFault)
        assertFalse(example.requiresQr)
    }

    @Test fun qrCasesExplainTheActualManualAction() {
        assertEquals(setOf("T02", "T06", "T09", "T10", "T11"),
            DemoCases.all.filter { it.requiresQr }.map { it.id }.toSet())
        val invalid = DemoCases.find("T06")!!
        assertTrue(invalid.steps.any { "仍等待" in it })
        assertTrue(invalid.steps.any { "再次打开扫码" in it })
        assertTrue(DemoCases.find("T09")!!.steps.any { "通知" in it && "取消" in it })
        assertTrue(DemoCases.find("T11")!!.steps.any { "45 秒" in it })
    }

    @Test fun recommendedCasesCoverNormalClarificationAndRecovery() {
        assertEquals(setOf("T02", "T04", "T12"),
            DemoCases.all.filter { it.recommended }.map { it.id }.toSet())
    }

    @Test fun runLabelsDoNotChangePromptsWithoutASubject() {
        val example = DemoCases.find("T06")!!
        assertEquals(example.promptTemplate, DemoCases.promptFor(example, "RUN-A"))
    }

    @Test(expected = IllegalArgumentException::class)
    fun missingRunLabelIsRejected() {
        DemoCases.promptFor(DemoCases.find("T02")!!, " ")
    }
}
