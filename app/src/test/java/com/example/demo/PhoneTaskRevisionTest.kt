package com.example.demo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PhoneTaskRevisionTest {
    @Test
    fun includesOnlyTaskKindAndPublicTransitContext() {
        val correction = "目的地换成广州塔"
        val current = PhoneIntent(
            type = PhoneIntentType.Transit,
            origin = "私人地址某小区二号楼",
            destination = "广东省博物馆",
            city = "广州",
            alias = "私人称呼",
            url = "https://example.org/private",
            recipient = "private@example.org",
            subject = "私密主题",
            body = "私密正文",
            shareText = "私密分享",
        )
        val prompt = phoneTaskRevisionPrompt(current, correction)
        assertTrue(prompt.contains("公交地铁"))
        assertTrue(prompt.contains("当前城市：广州"))
        assertTrue(prompt.contains("广东省博物馆"))
        assertTrue(prompt.endsWith(correction))
        for (privateValue in listOf(current.origin, current.alias, current.url, current.recipient,
                current.subject, current.body, current.shareText)) {
            assertFalse(prompt.contains(privateValue))
        }
        val family = current.copy(type = PhoneIntentType.Family)
        val familyPrompt = phoneTaskRevisionPrompt(family, correction)
        assertTrue(familyPrompt.contains("联系家人"))
        assertFalse(familyPrompt.contains("广东省博物馆"))
        assertFalse(familyPrompt.contains(family.alias))
        val long = "更".repeat(1500)
        assertEquals(long, phoneTaskRevisionPrompt(current, long))
    }

    @Test
    fun carriesOverOnlyMissingFieldsOfSameTransitTask() {
        val active = PhoneIntent(PhoneIntentType.Transit, origin = "板桥", destination = "广州塔", city = "广州")
        val change = PhoneIntent(PhoneIntentType.Transit, destination = "大学城南")
        assertEquals(active.copy(destination = "大学城南"), revisedPhoneIntent(active, change))
        assertEquals(change, revisedPhoneIntent(PhoneIntent(PhoneIntentType.Family), change))
        assertEquals(PhoneIntent(PhoneIntentType.WeChat, shareText = "请查收"), revisedPhoneIntent(
            active, PhoneIntent(PhoneIntentType.WeChat, shareText = "请查收"),
        ))
        assertNull(revisedPhoneIntent(active, PhoneIntent()))
        assertNull(revisedPhoneIntent(active, change.copy(destination = "https://example.org/")))
    }

    @Test
    fun appliesExplicitValidatedSingleFieldWithoutRequiringOtherFields() {
        val active = PhoneIntent(PhoneIntentType.Transit, origin = "广州塔", destination = "体育西", city = "广州")
        assertEquals(active.copy(origin = "板桥"), directPhoneTaskRevision(active, "出发地改成板桥"))
        assertEquals(active.copy(destination = "大学城南"), directPhoneTaskRevision(active, "请帮我把目的地改成大学城南"))
        assertEquals(active.copy(city = "深圳"), directPhoneTaskRevision(active, "城市改成在深圳。"))
        val incomplete = PhoneIntent(PhoneIntentType.Transit)
        assertEquals(incomplete.copy(city = "广州"), directPhoneTaskRevision(incomplete, "所在城市改成广州"))

        assertNull(directPhoneTaskRevision(active, "还是去板桥"))
        assertNull(directPhoneTaskRevision(active, "目的地改成广东省博物馆或者广州塔"))
        assertNull(directPhoneTaskRevision(active, "出发地改成板桥目的地改成大学城南"))
        assertNull(directPhoneTaskRevision(active, "城市改成https://example.org/"))
        assertNull(directPhoneTaskRevision(active, "目的地改成删除联系人"))
        assertNull(directPhoneTaskRevision(active, "出发地改成板桥\n"))
        assertNull(directPhoneTaskRevision(PhoneIntent(PhoneIntentType.Family), "出发地改成板桥"))
    }
}
