package com.example.demo

internal data class FamilyContact(val name: String, val number: String)

internal fun normalizeFamilyContactNumber(input: String): String? {
    val normalized = buildString {
        for (character in input) {
            when (character) {
                in '0'..'9' -> append(character)
                in '０'..'９' -> append('0' + (character - '０'))
                '+', '＋' -> if (isEmpty()) append('+') else return null
                ' ', '\u00A0', '\u3000', '-', '－', '(', ')', '（', '）' -> Unit
                else -> return null
            }
        }
    }
    val digits = normalized.removePrefix("+")
    if (digits.length !in 7..15 || (normalized.startsWith('+') && digits.startsWith('0'))) return null
    return normalized
}

internal fun validatedFamilyContact(name: String, number: String): FamilyContact? {
    val trimmed = normalizeFamilyContactName(name) ?: return null
    return normalizeFamilyContactNumber(number)?.let { FamilyContact(trimmed, it) }
}

internal fun normalizeFamilyContactName(input: String): String? = input.trim().takeIf {
    it.length in 1..40 && it.none(Char::isISOControl)
}
