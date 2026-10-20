package ru.audiotext.studio

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.provider.OpenableColumns
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import kotlinx.coroutines.*
import java.io.File
import java.util.Locale

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = lightColorScheme(
                primary = Color(0xFF315EDE),
                secondary = Color(0xFF6079B8),
                surface = Color(0xFFF9FAFF),
                background = Color(0xFFF4F6FB)
            )) {
                MainScreen(this)
            }
        }
    }

    fun share(files: List<File>) {
        if (files.isEmpty()) return
        val uris = ArrayList<Uri>(files.map {
            FileProvider.getUriForFile(this, packageName + ".files", it)
        })
        val intent = Intent(if (uris.size > 1) Intent.ACTION_SEND_MULTIPLE else Intent.ACTION_SEND)
        intent.type = "*/*"
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        if (uris.size > 1) {
            intent.putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris)
        } else {
            intent.putExtra(Intent.EXTRA_STREAM, uris.first())
        }
        startActivity(Intent.createChooser(intent, "Сохранить или поделиться результатами"))
    }

    fun displayName(uri: Uri): String {
        return try {
            contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) cursor.getString(0) else null
            } ?: "Выбранный файл"
        } catch (_: Exception) { "Выбранный файл" }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MainScreen(activity: MainActivity) {
    val scope = rememberCoroutineScope()
    var uri by remember { mutableStateOf<Uri?>(null) }
    var fileName by remember { mutableStateOf("Файл не выбран") }
    var mode by remember { mutableIntStateOf(0) } // 0 text+audio, 1 text only, 2 audio only
    var model by remember { mutableStateOf(Model.TINY) }
    var preferM4a by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Готово к работе") }
    var progress by remember { mutableFloatStateOf(0f) }
    var busy by remember { mutableStateOf(false) }
    var outputs by remember { mutableStateOf<List<File>>(emptyList()) }
    var worker by remember { mutableStateOf<Job?>(null) }
    var downloadedModels by remember { mutableIntStateOf(0) }

    fun report(message: String, value: Float? = null) {
        activity.runOnUiThread {
            status = message
            if (value != null) progress = value.coerceIn(0f, 1f)
        }
    }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { selected ->
        if (selected != null) {
            uri = selected
            fileName = activity.displayName(selected)
            outputs = emptyList()
            status = "Файл выбран"
            progress = 0f
        }
    }
    fun startProcessing(justDownload: Boolean) {
        if (busy) return
        val selected = uri
        if (!justDownload && selected == null) {
            status = "Сначала выбери аудио или видео"
            return
        }
        worker = scope.launch {
            busy = true
            outputs = emptyList()
            progress = 0f
            try {
                withContext(Dispatchers.IO) {
                    val active = coroutineContext[Job]!!
                    val verify: () -> Unit = { active.ensureActive() }
                    val modelPath = if (justDownload || mode != 2) {
                        OfflineWhisper.ensureModel(activity, model, { report(it) }, verify)
                    } else null
                    report("Модель готова", 0.05f)
                    if (!justDownload && selected != null) {
                        val dir = File(activity.getExternalFilesDir(Environment.DIRECTORY_DOCUMENTS), "AudioTextStudio")
                            .apply { mkdirs() }
                        val base = fileName.substringBeforeLast('.').replace(
                            Regex("[^\\p{L}\\p{N} ._-]"), "_"
                        ).trim().take(55).ifBlank { "Запись" }
                        val tag = System.currentTimeMillis().toString()
                        val prefix = base + "_" + tag
                        val tempWav = File(activity.cacheDir, "ats_" + tag + ".wav")
                        val saved = mutableListOf<File>()
                        try {
                            val needText = mode != 2
                            val needAudio = mode != 1
                            // With AAC passthrough, no audio conversion is needed in audio-only mode.
                            if (mode == 2 && preferM4a) {
                                report("Копирование AAC в M4A...", 0.15f)
                                val m4a = File(dir, prefix + ".m4a")
                                try {
                                    AudioPipeline.copyAacToM4a(activity, selected, m4a, verify)
                                    saved.add(m4a)
                                    report("Готово", 1f)
                                } catch (e: Exception) {
                                    m4a.delete()
                                    throw e
                                }
                            } else {
                                report("Извлечение аудио...", 0.05f)
                                AudioPipeline.decodeToWav(activity, selected, tempWav,
                                    { report("Обработка звука: " + (it * 100).toInt() + "%", 0.05f + it * 0.35f) },
                                    verify
                                )
                                if (needAudio) {
                                    if (preferM4a) {
                                        val m4a = File(dir, prefix + ".m4a")
                                        try {
                                            report("Копирование AAC-дорожки...", 0.41f)
                                            AudioPipeline.copyAacToM4a(activity, selected, m4a, verify)
                                            saved.add(m4a)
                                        } catch (e: IllegalStateException) {
                                            m4a.delete()
                                            report("AAC не найден, сохраняю WAV", 0.42f)
                                            val outputWav = File(dir, prefix + ".wav")
                                            tempWav.copyTo(outputWav)
                                            saved.add(outputWav)
                                        }
                                    } else {
                                        val outputWav = File(dir, prefix + ".wav")
                                        tempWav.copyTo(outputWav)
                                        saved.add(outputWav)
                                    }
                                }
                                if (needText) {
                                    report("Расшифровка на телефоне...", 0.44f)
                                    val segments = OfflineWhisper.transcribe(tempWav, requireNotNull(modelPath),
                                        { report("Распознавание: " + (it * 100).toInt() + "%", 0.44f + it * 0.54f) },
                                        verify
                                    )
                                    val txt = File(dir, prefix + ".txt")
                                    val srt = File(dir, prefix + ".srt")
                                    txt.writeText(OfflineWhisper.plainText(segments), Charsets.UTF_8)
                                    srt.writeText(OfflineWhisper.subtitles(segments), Charsets.UTF_8)
                                    saved.add(txt)
                                    saved.add(srt)
                                }
                                report("Готово", 1f)
                            }
                            verify()
                            withContext(Dispatchers.Main) { outputs = saved.toList() }
                        } finally { tempWav.delete() }
                    }
                }
                downloadedModels++
                if (justDownload) status = "Модель загружена. Можно работать офлайн."
            } catch (_: CancellationException) {
                status = "Операция остановлена"
            } catch (e: Exception) {
                status = "Ошибка: " + (e.message ?: e.javaClass.simpleName)
            } finally { busy = false }
        }
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 24.dp),
        verticalArrangement = Arrangement.spacedBy(18.dp)
    ) {
        Text("AudioText Studio", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Text("Аудио из видео и речь в текст — прямо на Android",
            color = MaterialTheme.colorScheme.onSurfaceVariant)

        ElevatedCard(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("1. Выбери запись", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleMedium)
                Text(fileName, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Button(onClick = { picker.launch(arrayOf("audio/*", "video/*")) },
                    enabled = !busy, modifier = Modifier.fillMaxWidth()) {
                    Text("Выбрать аудио или видео")
                }
            }
        }
        ElevatedCard(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("2. Что сделать?", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleMedium)
                listOf("Извлечь звук + распознать", "Только распознать речь", "Только извлечь звук")
                    .forEachIndexed { index, label ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            RadioButton(selected = mode == index, enabled = !busy, onClick = { mode = index })
                            Text(label)
                        }
                    }
                if (mode != 1) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = preferM4a, enabled = !busy, onCheckedChange = { preferM4a = it })
                        Column {
                            Text("Предпочитать M4A без потери качества")
                            Text("Для AAC-дорожек; иначе WAV", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
        if (mode != 2) {
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("3. Офлайн-модель Whisper", fontWeight = FontWeight.Bold,
                        style = MaterialTheme.typography.titleMedium)
                    Model.entries.forEach { option ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            RadioButton(selected = model == option, enabled = !busy, onClick = { model = option })
                            Text(option.label)
                        }
                    }
                    val hasModel = OfflineWhisper.modelFile(activity, model).length() >= model.minimumBytes
                    Text(if (hasModel) "Модель сохранена на телефоне" else "Модель нужно скачать один раз",
                        color = if (hasModel) Color(0xFF26744B) else MaterialTheme.colorScheme.onSurfaceVariant)
                    OutlinedButton(onClick = { startProcessing(true) }, enabled = !busy,
                        modifier = Modifier.fillMaxWidth()) {
                        Text("Скачать выбранную модель")
                    }
                }
            }
        }
        Button(onClick = { startProcessing(false) }, enabled = !busy && uri != null,
            modifier = Modifier.fillMaxWidth().height(52.dp)) {
            Text(if (busy) "Обработка..." else "Начать обработку")
        }
        if (busy) {
            OutlinedButton(onClick = { worker?.cancel() }, modifier = Modifier.fillMaxWidth()) {
                Text("Остановить")
            }
        }
        LinearProgressIndicator(progress = { progress }, modifier = Modifier.fillMaxWidth())
        Text(status, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (outputs.isNotEmpty()) {
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Результаты", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    outputs.forEach { Text("• " + it.name, style = MaterialTheme.typography.bodySmall) }
                    Button(onClick = { activity.share(outputs) }, modifier = Modifier.fillMaxWidth()) {
                        Text("Поделиться или сохранить")
                    }
                }
            }
        }
        Text("Обработка идёт на устройстве. Для длительных записей держи приложение открытым. " +
            "Поддерживаются современные телефоны с 64-битным ARM.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(20.dp))
    }
}
