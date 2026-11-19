package com.example.demo

import android.util.AtomicFile
import java.io.File
import java.io.FileNotFoundException
import java.io.IOException
import org.json.JSONException
import org.json.JSONObject

internal class FamilyContactStore(directory: File) {
    private val file = AtomicFile(File(directory, "family_contact.json"))

    fun read(): FamilyContact? = synchronized(diskLock) {
        val raw = try {
            file.openRead().bufferedReader(Charsets.UTF_8).use { it.readText() }
        } catch (error: FileNotFoundException) {
            if (file.baseFile.exists()) throw error
            return@synchronized null
        }
        try {
            val json = JSONObject(raw)
            validatedFamilyContact(json.getString("name"), json.getString("number"))
                ?: throw IOException("Invalid saved family contact")
        } catch (error: JSONException) {
            throw IOException("Invalid saved family contact", error)
        }
    }

    fun write(contact: FamilyContact) = synchronized(diskLock) {
        if (validatedFamilyContact(contact.name, contact.number) != contact) throw IOException("Invalid family contact")
        val bytes = JSONObject().put("name", contact.name).put("number", contact.number)
            .toString().toByteArray(Charsets.UTF_8)
        val stream = file.startWrite()
        try {
            stream.write(bytes)
            file.finishWrite(stream)
        } catch (error: IOException) {
            file.failWrite(stream)
            throw error
        }
        if (read() != contact) throw IOException("Could not save family contact")
    }

    fun clear() = synchronized(diskLock) {
        file.delete()
        if (read() != null) throw IOException("Could not remove family contact")
    }

    private companion object {
        val diskLock = Any()
    }
}
