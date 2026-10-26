package com.example.demo

data class TaskInstruction(val recipient: String, val subject: String, val body: String, val url: String?)

// Supported sharing task: extract explicit values; ambiguous contacts require an email address.
fun parseTaskInstruction(text: String): TaskInstruction {
    val recipient = Regex("[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Za-z]{2,}").find(text)?.value.orEmpty()
    val url = Regex("https://[^\\s，。；]+").find(text)?.value
    val subject = Regex("主题(?:是|为|：|:)\\s*(.+?)(?=[，,；;]?\\s*(?:正文|内容)(?:是|为|：|:)|$)")
        .find(text)?.groupValues?.get(1)?.trim()?.trimEnd('，', ',', '；', ';') ?: "网页分享"
    val body = Regex("(?:正文|内容)(?:是|为|：|:)\\s*(.+)").find(text)?.groupValues?.get(1).orEmpty()
    return TaskInstruction(recipient, subject, body, url)
}
