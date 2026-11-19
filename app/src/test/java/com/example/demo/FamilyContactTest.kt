package com.example.demo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class FamilyContactTest {
    @Test
    fun normalizesCommonMobileLandlineAndInternationalFormats() {
        assertEquals("13800138000", normalizeFamilyContactNumber("１３８ ００１３-８０００"))
        assertEquals("02055550123", normalizeFamilyContactNumber("（020） 5555-0123"))
        assertEquals("+8613800138000", normalizeFamilyContactNumber("＋86\u3000138\u00A00013－8000"))
        assertEquals("5550123", normalizeFamilyContactNumber("555-0123"))
        assertEquals("+123456789012345", normalizeFamilyContactNumber("+123456789012345"))
    }

    @Test
    fun rejectsEmptyShortAndOversizedNumbers() {
        listOf("", "  ", "() -", "+", "110", "123456", "1234567890123456", "+08612345")
            .forEach { assertNull(it, normalizeFamilyContactNumber(it)) }
    }

    @Test
    fun rejectsServiceCodesExtensionsAndIntentSyntax() {
        listOf("*#06#", "1234567*", "1234567#1", "1234567,12", "1234567;12", "1234567x12",
            "tel:1234567", "1234567%3B12", "1234567\n8912", "123+4567", "++1234567")
            .forEach { assertNull(it, normalizeFamilyContactNumber(it)) }
    }

    @Test
    fun contactRequiresANameAndAnAcceptedNumber() {
        assertEquals(FamilyContact("家人", "5550123"), validatedFamilyContact(" 家人 ", "555-0123"))
        assertNull(validatedFamilyContact("  ", "5550123"))
        assertNull(validatedFamilyContact("家\n人", "5550123"))
        assertNull(validatedFamilyContact("家".repeat(41), "5550123"))
        assertNull(validatedFamilyContact("家人", "110"))
    }
}
