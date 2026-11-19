package com.example.demo

import org.junit.Assert.*
import org.junit.Test

class DemoCasesTest {
    @Test
    fun defaultMapDemoFinishesInNativeNavigation() {
        val example = checkNotNull(DemoCases.find("T13"))
        assertTrue(example.recommended)
        assertTrue(example.steps.any { "开始高德导航" in it })
        assertFalse("钉钉" in example.promptTemplate)
    }

    @Test fun visibleCasesHaveUniqueStableIdsWithoutTheOldWebOnlyCase() {
        assertEquals((2..20).map { "T%02d".format(it) }, DemoCases.all.map { it.id })
        assertEquals(19, DemoCases.all.map { it.id }.toSet().size)
        DemoCases.all.forEach { assertEquals(it, DemoCases.find(it.id)) }
        assertNull(DemoCases.find("T01"))
        assertNull(DemoCases.find("not-a-case"))
    }

    @Test fun theSingleNormalDemoIsContinuousAndOtherCasesKeepTheirSteps() {
        val normalCases = DemoCases.visible(DemoDomain.MAIL, DemoFilter.SUCCESS)
        assertEquals(listOf("T02"), normalCases.map { it.id })
        assertEquals("完整扫码发信演示", normalCases.single().title)
        assertTrue(normalCases.single().steps.isEmpty())
        assertEquals(DemoCategory.CHECKS, DemoCases.find("T03")!!.category)
        DemoCases.all.forEach {
            assertTrue(it.title.isNotBlank())
            assertTrue(it.summary.isNotBlank())
            assertTrue(it.promptTemplate.isNotBlank())
            if (it.category != DemoCategory.SUCCESS) assertTrue(it.steps.size in 3..5)
            else assertTrue(it.steps.isEmpty())
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
        assertEquals(setOf("T02", "T06", "T09", "T10", "T11", "T12"),
            DemoCases.all.filter { it.requiresQr }.map { it.id }.toSet())
        val invalid = DemoCases.find("T06")!!
        assertTrue(invalid.steps.any { "仍等待" in it })
        assertTrue(invalid.steps.any { "再次打开扫码" in it })
        assertTrue(DemoCases.find("T09")!!.steps.any { "通知" in it && "取消" in it })
        assertTrue(DemoCases.find("T11")!!.steps.any { "45 秒" in it })
        assertTrue(DemoCases.find("T12")!!.steps.any { "扫描" in it && "桌面" in it })
    }

    @Test fun recommendedCasesCoverNormalClarificationAndRecovery() {
        assertEquals(setOf("T02", "T04", "T12", "T13", "T15", "T18"),
            DemoCases.all.filter { it.recommended }.map { it.id }.toSet())
    }

    @Test fun runLabelsDoNotChangePromptsWithoutASubject() {
        val example = DemoCases.find("T06")!!
        assertEquals(example.promptTemplate, DemoCases.promptFor(example, "RUN-A"))
    }

    @Test fun routeExamplesStaySeparateFromMailAndFaultInjection() {
        val routes = DemoCases.all.filter { it.domain == DemoDomain.MAP }
        assertEquals((13..20).map { "T%02d".format(it) }, routes.map { it.id })
        routes.forEach {
            assertNull(it.recoveryFault)
            assertFalse(it.requiresQr)
            assertFalse(it.promptTemplate.contains("主题是"))
            assertEquals("查询公交地铁路线", it.startLabel)
        }
        assertTrue(routes.first().promptTemplate.contains("从广州塔地铁站"))
        assertTrue(DemoCases.find("T14")!!.promptTemplate.contains("当前位置"))
    }

    @Test fun bothDomainsOfferNormalExceptionsAndRecoveryWithoutMixingCases() {
        val expected = mapOf(
            DemoFilter.RECOMMENDED to listOf("T13", "T15", "T18"),
            DemoFilter.SUCCESS to listOf("T13", "T14"),
            DemoFilter.CHECKS to listOf("T15", "T16", "T17"),
            DemoFilter.RECOVERY to listOf("T18", "T19", "T20"),
        )
        expected.forEach { (filter, ids) ->
            assertEquals(ids, DemoCases.visible(DemoDomain.MAP, filter).map { it.id })
            assertTrue(DemoCases.visible(DemoDomain.MAIL, filter).all { it.domain == DemoDomain.MAIL })
        }
        assertEquals(listOf("T02", "T04", "T12"), DemoCases.visible(DemoDomain.MAIL, DemoFilter.RECOMMENDED).map { it.id })
        assertEquals(listOf("T12"), DemoCases.visible(DemoDomain.MAIL, DemoFilter.RECOVERY).map { it.id })
    }

    @Test fun mapRecoveryExamplesDescribeRealUserActionsInsteadOfRequestingAFakeFault() {
        val clarify = DemoCases.find("T18")!!
        assertTrue(clarify.promptTemplate.contains("从家里"))
        assertTrue(clarify.steps.any { it.contains("体育西路地铁站") })
        val network = DemoCases.find("T20")!!
        assertTrue(network.steps.any { "本人" in it && "关闭" in it })
        assertTrue(network.steps.any { "故障未复现" in it })
        assertTrue(network.steps.any { "重新查询" in it })
        assertFalse(network.promptTemplate.contains("断网"))
        assertEquals("开始完整演示", DemoCases.find("T02")!!.startLabel)
    }

    @Test(expected = IllegalArgumentException::class)
    fun missingRunLabelIsRejected() {
        DemoCases.promptFor(DemoCases.find("T02")!!, " ")
    }
}
