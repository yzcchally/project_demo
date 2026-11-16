package com.example.demo

import android.content.Intent
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class TransitRouteShareTest {
    private fun currentVerifiedTask() = JSONObject(
        """
        {
            "id": "active-task",
            "query_id": "current-query",
            "state": "completed",
            "route_verified": true,
            "transit_result": {
                "task_id": "active-task",
                "query_id": "current-query",
                "route_verified": true,
                "classification": "COMPLETE",
                "source_app": "高德地图",
                "queried_at": "2026-11-11T16:38:15.091244+00:00",
                "missing_fields": [],
                "origin": {
                    "name": "住址示例南路甲栋101室",
                    "address": "嵌套敏感门牌",
                    "source": "current_location"
                },
                "destination": {"name": "住宅示例东街乙栋303室"},
                "duration_minutes": 21.5,
                "transfers": 1,
                "walking_meters": 410.0,
                "fare_yuan": null,
                "evidence": [{"text": "原始定位记录"}],
                "segments": [
                    {"mode": "walk", "board": "住址示例南路甲栋101室", "alight": "体育西路",
                     "instructions": "从家庭地址示例步行", "walking_meters": 170, "duration_minutes": 3},
                    {"mode": "subway", "line": "3号线", "board": "体育西路", "direction": "开往海傍方向",
                     "stops": 2, "alight": "广州塔"},
                    {"mode": "bus", "line": "示例公交", "board": "广州塔", "direction": "森林公园",
                     "alight": "滨江东路总站"},
                    {"mode": "walk", "board": "滨江东路总站", "alight": "住宅示例东街乙栋303室",
                     "instructions": "站内外共步行"}
                ]
            }
        }
        """.trimIndent(),
    )

    @Test
    fun previewSharesVerifiedLegsAndMissingFactsWithoutPrivateEndpoints() {
        val text = checkNotNull(transitRouteShareText(currentVerifiedTask()))
        assertTrue(text.contains("查询时间（本机时间）：${deviceQueryTime("2026-11-11T16:38:15.091244+00:00")}"))
        assertTrue(text.contains("乘车范围：体育西路 → 滨江东路总站"))
        assertTrue(text.contains("预计用时：21.5 分钟 · 换乘：1 次 · 总步行：410 米 · 预计票价：未显示"))
        assertTrue(text.contains("1. 步行，约 170 米，约 3 分钟，具体走法请查看高德地图"))
        assertTrue(text.contains("2. 乘坐地铁 3号线，在体育西路上车，往海傍方向，乘坐 2 站，在广州塔下车"))
        assertTrue(text.contains("3. 乘坐公交 示例公交，在广州塔上车，往森林公园方向，在滨江东路总站下车"))
        assertTrue(text.contains("4. 步行，具体走法请查看高德地图"))
        assertTrue(text.contains("未读取：第 3 步站数、预计票价。"))
        listOf("101室", "303室", "嵌套敏感门牌", "家庭地址示例", "原始定位记录", "current-query").forEach {
            assertFalse("Preview leaked: $it", text.contains(it))
        }
    }

    @Test
    fun missingQueryTimeIsNotReplacedWithTheShareTime() {
        val task = currentVerifiedTask()
        task.getJSONObject("transit_result").remove("queried_at")
        assertTrue(checkNotNull(transitRouteShareText(task)).contains("查询时间（本机时间）：待核对"))
    }

    @Test
    fun onlyCompletedAndIndependentlyVerifiedCurrentAmapRoutesCanBeShared() {
        assertNotNull(transitRouteShareText(currentVerifiedTask()))
        for (state in listOf("partial", "no_route", "failed", "cancelled", "planning_transit", "outcome_unknown")) {
            assertNull(state, transitRouteShareText(currentVerifiedTask().put("state", state)))
        }
        assertNull(transitRouteShareText(currentVerifiedTask().put("route_verified", false)))
        assertNull(transitRouteShareText(currentVerifiedTask().put("query_id", "")))
        val invalidResultFields = mapOf(
            "task_id" to "different-task",
            "query_id" to "old-query",
            "classification" to "PARTIAL",
            "source_app" to "未知应用",
            "route_verified" to "true",
            "missing_fields" to JSONArray().put("segments.ride.alight"),
            "segments" to JSONArray(),
        )
        for ((field, value) in invalidResultFields) {
            val task = currentVerifiedTask()
            task.getJSONObject("transit_result").put(field, value)
            assertNull(field, transitRouteShareText(task))
        }
        val historyOnly = currentVerifiedTask()
        historyOnly.put("transit_history", JSONArray().put(historyOnly.remove("transit_result")))
        assertNull(transitRouteShareText(historyOnly))
    }

    @Test
    fun shareIntentsCarryOnlyPlainTextAndTheRequestedPackage() {
        val text = checkNotNull(transitRouteShareText(currentVerifiedTask()))
        val dingtalk = transitShareIntent(text, dingtalk = true)
        val system = transitShareIntent(text)
        for (intent in listOf(dingtalk, system)) {
            assertEquals(Intent.ACTION_SEND, intent.action)
            assertEquals("text/plain", intent.type)
            assertEquals(text, intent.getStringExtra(Intent.EXTRA_TEXT))
            assertEquals(setOf(Intent.EXTRA_TEXT), intent.extras?.keySet())
            assertNull(intent.component)
            assertNull(intent.data)
            assertNull(intent.clipData)
        }
        assertEquals("com.alibaba.android.rimet", dingtalk.`package`)
        assertNull(system.`package`)
    }
}
