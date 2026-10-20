package ru.audiotext.studio

import android.content.Context
import java.io.File
import java.io.RandomAccessFile
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale

object NativeWhisper {
    init { System.loadLibrary("audiotext_jni") }
    external fun open(path: String): Long
    external fun recognize(handle: Long, samples: FloatArray): Array<String>
    external fun close(handle: Long)
}

enum class Model(val label: String, val filename: String, val minimumBytes: Long) {
    TINY("Быстро · tiny (75 МБ)", "ggml-tiny.bin", 60_000_000L),
    BASE("Точнее · base (142 МБ)", "ggml-base.bin", 120_000_000L)
}
data class Segment(val startMs: Long, val endMs: Long, val text: String)

object OfflineWhisper {
    fun modelFile(context: Context, model: Model) =
        File(File(context.filesDir, "models").apply { mkdirs() }, model.filename)

    fun ensureModel(context: Context, model: Model, status: (String) -> Unit, verify: () -> Unit): File {
        val destination = modelFile(context, model)
        if (destination.length() >= model.minimumBytes) return destination
        val temporary = File(destination.parentFile, destination.name + ".part")
        try {
            val url = URL("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/" + model.filename)
            val conn = url.openConnection() as HttpURLConnection
            conn.connectTimeout = 20_000
            conn.readTimeout = 45_000
            conn.instanceFollowRedirects = true
            conn.setRequestProperty("User-Agent", "AudioTextStudioAndroid/0.1")
            try {
                verify()
                conn.connect()
                require(conn.responseCode == 200) { "Сервер модели: HTTP " + conn.responseCode }
                val length = conn.contentLengthLong
                var written = 0L
                conn.inputStream.use { input ->
                    temporary.outputStream().buffered().use { output ->
                        val buffer = ByteArray(64 * 1024)
                        while (true) {
                            verify()
                            val n = input.read(buffer)
                            if (n < 0) break
                            output.write(buffer, 0, n)
                            written += n
                            if (written % (1024 * 1024) < buffer.size || written == n.toLong()) {
                                val percent = if (length > 0) " (" + written * 100 / length + "%)" else ""
                                status("Загрузка модели " + model.label + percent)
                            }
                        }
                    }
                }
                require(temporary.length() >= model.minimumBytes) {
                    "Модель загрузилась не полностью. Проверь подключение и повтори."
                }
                require(!destination.exists() || destination.delete()) { "Не удалось заменить модель" }
                require(temporary.renameTo(destination)) { "Не удалось сохранить модель" }
            } finally { conn.disconnect() }
            return destination
        } catch (e: Exception) {
            temporary.delete()
            throw e
        }
    }

    fun transcribe(wav: File, model: File, update: (Float) -> Unit, verify: () -> Unit,
        onChunk: (Long, Long, Int, List<Segment>) -> Unit = { _, _, _, _ -> }
    ): List<Segment> {
        require(model.isFile) { "Модель Whisper не найдена" }
        val handle = NativeWhisper.open(model.absolutePath)
        require(handle != 0L) { "Не удалось открыть модель Whisper" }
        val result = mutableListOf<Segment>()
        try {
            RandomAccessFile(wav, "r").use { source ->
                require(source.length() > 44) { "Аудиофайл пустой" }
                source.seek(44)
                val totalBytes = source.length() - 44
                val bytesPerChunk = 25 * 16_000 * 2
                val chunk = ByteArray(bytesPerChunk)
                var position = 0L
                while (position < totalBytes) {
                    verify()
                    val count = source.read(chunk, 0, minOf(bytesPerChunk.toLong(), totalBytes - position).toInt())
                    if (count <= 0) break
                    val frameCount = count / 2
                    val floats = FloatArray(maxOf(frameCount, 16_000))
                    for (i in 0 until frameCount) {
                        val raw = (chunk[i * 2].toInt() and 255) or (chunk[i * 2 + 1].toInt() shl 8)
                        floats[i] = raw.toShort() / 32768f
                    }
                    val offsetMs = position / 32
                    val newSegments = mutableListOf<Segment>()
                    NativeWhisper.recognize(handle, floats).forEach { encoded ->
                        val fields = encoded.split('\t', limit = 3)
                        if (fields.size == 3) {
                            val value = fields[2].trim()
                            val start = offsetMs + (fields[0].toLongOrNull() ?: 0L)
                            val end = offsetMs + (fields[1].toLongOrNull() ?: 0L)
                            if (value.isNotBlank() && end > start) {
                                val segment = Segment(start, end, value)
                                result.add(segment)
                                newSegments.add(segment)
                            }
                        }
                    }
                    position += count
                    update((position.toFloat() / totalBytes).coerceIn(0f, 1f))
                    onChunk(position / 32000, (totalBytes + 31999) / 32000,
                        ((position + bytesPerChunk - 1) / bytesPerChunk).toInt(), newSegments)
                }
            }
        } finally { NativeWhisper.close(handle) }
        return result
    }

    fun plainText(segments: List<Segment>): String =
        segments.joinToString("\n") { "[" + formatTime(it.startMs, false) + "] " + it.text } + "\n"

    fun subtitles(segments: List<Segment>): String =
        segments.mapIndexed { index, seg ->
            (index + 1).toString() + "\n" + formatTime(seg.startMs, true) + " --> " +
                formatTime(seg.endMs, true) + "\n" + seg.text + "\n"
        }.joinToString("\n")

    private fun formatTime(ms: Long, comma: Boolean): String {
        val m = ms.coerceAtLeast(0)
        val h = m / 3_600_000
        val min = m / 60_000 % 60
        val s = m / 1_000 % 60
        val remainder = m % 1_000
        return String.format(Locale.US, "%02d:%02d:%02d%s%03d", h, min, s, if (comma) "," else ".", remainder)
    }
}
