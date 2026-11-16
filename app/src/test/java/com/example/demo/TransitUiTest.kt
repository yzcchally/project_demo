package com.example.demo

import org.junit.Assert.*
import org.junit.Test

class TransitUiTest {
    @Test fun completionRequiresVerifiedCurrentRouteAndConfirmedDingTalkWhenRequested() {
        assertTrue(transitCompletionReady("completed", true, "COMPLETE", true, false, "not_requested"))
        assertTrue(transitCompletionReady("completed", true, "COMPLETE", true, true, "sent"))
        assertFalse(transitCompletionReady("completed", true, "COMPLETE", true, true, "unknown"))
        assertFalse(transitCompletionReady("completed", true, "COMPLETE", false, true, "sent"))
        assertFalse(transitCompletionReady("completed", false, "COMPLETE", true, false, "not_requested"))
        assertFalse(transitCompletionReady("completed", true, "PARTIAL", true, false, "not_requested"))
        for (state in listOf("failed", "cancelled", "partial", "no_route", "outcome_unknown", "interrupted", "sharing_transit")) {
            assertFalse(transitCompletionReady(state, true, "COMPLETE", true, true, "sent"))
        }
    }
    @Test fun clarificationRequiresAnExplicitOriginBeforeSubmitting() {
        assertFalse(transitClarificationReady("", "广东省博物馆", "广州市", setOf("origin")))
        assertFalse(transitClarificationReady("  ", "广东省博物馆", "广州市", setOf("origin")))
        assertTrue(transitClarificationReady("体育西路地铁站", "广东省博物馆", "广州市", setOf("origin")))
    }

    @Test fun clarificationChecksRequiredCityAndDestination() {
        assertFalse(transitClarificationReady("体育西路地铁站", "广东省博物馆", "", setOf("city")))
        assertFalse(transitClarificationReady("体育西路地铁站", "", "广州市", setOf("destination")))
        assertTrue(transitClarificationReady("体育西路地铁站", "广东省博物馆", "广州市", setOf("city")))
    }

    @Test fun queryTimeConvertsUtcAcrossShanghaiMidnight() {
        assertEquals("2026年11月12日 00:38", transitQueryTime("2026-11-11T16:38:15.091244+00:00", java.time.ZoneId.of("Asia/Shanghai")))
    }

    @Test fun queryTimeUsesTheExplicitZoneAndRejectsInvalidInput() {
        val zone = java.time.ZoneId.of("Asia/Shanghai")
        assertEquals("2026年11月12日 10:38", transitQueryTime("2026-11-12T02:38:00Z", zone))
        assertEquals("待核对", transitQueryTime("invalid-time", zone))
    }

    @Test fun explicitOriginAlwaysOverridesCurrentLocation() {
        val text = transitInstruction(" 广州塔地铁站 ", "体育西路地铁站", "广州市", "fewer_transfers", true)
        assertTrue(text.contains("从广州塔地铁站去体育西路地铁站"))
        assertTrue(text.contains("尽量少换乘"))
        assertFalse(text.contains("当前位置"))
    }

    @Test fun destinationOnlyMayRequestRealLocationWithoutInventingACity() {
        val text = transitInstruction("", "图书馆", "", "recommended", true)
        assertEquals("从当前位置去图书馆，坐地铁或公交，选择高德推荐方案。", text)
        assertFalse(text.contains("广州市"))
    }

    @Test fun missingOriginWithoutLocationPermissionStaysMissing() {
        assertEquals("我想去公园，坐地铁或公交，尽量少步行。", transitInstruction("", "公园", "", "less_walking", false))
    }

    @Test(expected = IllegalArgumentException::class)
    fun emptyDestinationCannotStartAQuery() {
        transitInstruction("车站", " ", "广州", "recommended", false)
    }

    @Test fun missingTransitFactsAreNotInventedByNarration() {
        val text = TransitStep(mode = "subway", line = "3号线", board = "广州塔", alight = "体育西路").text()
        assertTrue(text.contains("3号线"))
        assertTrue(text.contains("广州塔上车"))
        assertTrue(text.contains("体育西路下车"))
        assertFalse(text.contains("方向"))
        assertFalse(text.contains("出口"))
        assertFalse(text.contains("null"))
    }

    @Test fun EachLegKeepsItsOwnStopsAndDirection() {
        val first = TransitStep("subway", "3号线", "天河客运站", "广州塔", "体育西路", "3").text()
        val second = TransitStep("subway", "1号线", "西塱", "体育西路", "公园前", "5").text()
        assertTrue(first.contains("在体育西路下车"))
        assertFalse(first.contains("公园前"))
        assertTrue(second.contains("往西塱方向"))
        assertTrue(second.contains("在公园前下车"))
        listOf("海傍方向", "往海傍方向", "开往海傍方向").forEach { observed ->
            val direct = TransitStep("subway", "3号线", observed, "体育西路", "广州塔", "2").text()
            assertTrue(direct.contains("往海傍方向"))
            assertFalse(direct.contains("方向方向"))
            assertFalse(direct.contains("往往"))
        }
    }

    @Test fun WalkingTextDoesNotPromiseAccessibility() {
        val text = TransitStep("walk", instructions = "步行到A出口", exit = "A出口").text()
        assertTrue(text.contains("步行到A出口"))
        assertFalse(text.contains("无台阶"))
        assertFalse(text.contains("电梯"))
    }

    @Test fun aggregateWalkingKeepsItsVerifiedEndpointsWithoutAssigningLegMetrics() {
        val text = TransitStep("walk", board = "珠江新城C口", alight = "广东省博物馆", instructions = "站内外共步行").text()
        assertTrue(text.contains("从珠江新城C口"))
        assertTrue(text.contains("走到广东省博物馆"))
        assertTrue(text.contains("具体街道走法未显示"))
        assertFalse(text.contains("876"))
        assertFalse(text.contains("15分钟"))
    }

    @Test fun walkingMetricsDoNotPromiseTurnByTurnStreetDirections() {
        val text = TransitStep("walk", board = "测试起点", alight = "板桥C口", instructions = "步行 2.4公里 37分钟").text()
        assertTrue(text.contains("从测试起点"))
        assertTrue(text.contains("走到板桥C口"))
        assertTrue(text.contains("2.4公里 37分钟"))
        assertTrue(text.contains("具体街道走法未显示"))
    }

    @Test fun PartialAndNoRouteAreTerminalWithoutMailEvidence() {
        assertTrue("partial" in transitTerminalStates)
        assertTrue("no_route" in transitTerminalStates)
        assertFalse("awaiting_place_choice" in transitTerminalStates)
        assertFalse("paused" in transitTerminalStates)
    }

    @Test fun missingFactsHaveReadableLabelsInsteadOfInternalPaths() {
        assertEquals("第2段下车站", transitMissingLabel("segments[1].alight"))
        assertEquals("城市", transitMissingLabel("city"))
        assertEquals("部分路线详情", transitMissingLabel("unknown_field"))
    }
}
