package ru.audiotext.studio

import android.content.Context
import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import android.net.Uri
import java.io.Closeable
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.roundToInt

/**
 * Decodes the selected media's audio track using the device codec into a streaming,
 * 16 kHz, mono, 16-bit WAV file. Does not decode video frames or load the audio in RAM.
 */
object AudioPipeline {
    private const val TARGET_RATE = 16000

    fun decodeToWav(context: Context, uri: Uri, wav: File, progress: (Float) -> Unit, check: () -> Unit) {
        val afd = context.contentResolver.openAssetFileDescriptor(uri, "r")
            ?: error("Не удалось открыть выбранный файл")
        val extractor = MediaExtractor()
        var codec: MediaCodec? = null
        try {
            extractor.setDataSource(afd.fileDescriptor, afd.startOffset, afd.length)
            val audioTrack = (0 until extractor.trackCount).firstOrNull {
                extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true
            } ?: error("Аудиодорожка в файле не найдена")
            extractor.selectTrack(audioTrack)
            val trackFormat = extractor.getTrackFormat(audioTrack)
            val mime = trackFormat.getString(MediaFormat.KEY_MIME) ?: error("Неизвестный аудиокодек")
            val duration = if (trackFormat.containsKey(MediaFormat.KEY_DURATION))
                trackFormat.getLong(MediaFormat.KEY_DURATION) else 0L
            codec = MediaCodec.createDecoderByType(mime)
            codec.configure(trackFormat, null, null, 0)
            codec.start()
            WavSink(wav).use { sink ->
                var resampler: Resampler? = null
                var endedInput = false
                var endedOutput = false
                val info = MediaCodec.BufferInfo()
                while (!endedOutput) {
                    check()
                    if (!endedInput) {
                        val inputId = codec.dequeueInputBuffer(10_000)
                        if (inputId >= 0) {
                            val buffer = codec.getInputBuffer(inputId) ?: error("No input buffer")
                            buffer.clear()
                            val count = extractor.readSampleData(buffer, 0)
                            if (count < 0) {
                                codec.queueInputBuffer(inputId, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                                endedInput = true
                            } else {
                                val pts = extractor.sampleTime.coerceAtLeast(0)
                                codec.queueInputBuffer(inputId, 0, count, pts, 0)
                                if (duration > 0) progress((pts.toFloat() / duration).coerceIn(0f, 1f))
                                extractor.advance()
                            }
                        }
                    }
                    when (val outputId = codec.dequeueOutputBuffer(info, 10_000)) {
                        MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                            val of = codec.outputFormat
                            val rate = of.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                            val channels = of.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                            require(rate > 0 && channels > 0) { "Неподдерживаемый формат PCM" }
                            resampler = Resampler(rate, channels, sink)
                        }
                        MediaCodec.INFO_TRY_AGAIN_LATER -> Unit
                        else -> if (outputId >= 0) {
                            val buffer = codec.getOutputBuffer(outputId)
                            if (info.size > 0 && buffer != null) {
                                val of = codec.outputFormat
                                val rate = of.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                                val channels = of.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                                if (resampler == null) resampler = Resampler(rate, channels, sink)
                                val encoding = if (of.containsKey(MediaFormat.KEY_PCM_ENCODING))
                                    of.getInteger(MediaFormat.KEY_PCM_ENCODING) else AudioFormat.ENCODING_PCM_16BIT
                                buffer.position(info.offset)
                                buffer.limit(info.offset + info.size)
                                resampler.consume(buffer.slice().order(ByteOrder.LITTLE_ENDIAN), encoding)
                            }
                            endedOutput = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                            codec.releaseOutputBuffer(outputId, false)
                        }
                    }
                }
                check()
                progress(1f)
            }
        } catch (e: Exception) {
            wav.delete()
            throw e
        } finally {
            try { codec?.stop() } catch (_: Exception) {}
            codec?.release()
            extractor.release()
            afd.close()
        }
    }

    /** Lossless pass-through for AAC tracks only. WAV export is available for other codecs. */
    fun copyAacToM4a(context: Context, uri: Uri, target: File, check: () -> Unit) {
        val afd = context.contentResolver.openAssetFileDescriptor(uri, "r") ?: error("Не удалось открыть файл")
        val extractor = MediaExtractor()
        var muxer: MediaMuxer? = null
        var started = false
        try {
            extractor.setDataSource(afd.fileDescriptor, afd.startOffset, afd.length)
            val index = (0 until extractor.trackCount).firstOrNull {
                extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME) == MediaFormat.MIMETYPE_AUDIO_AAC
            } ?: error("В файле нет AAC-дорожки. Выбери WAV.")
            extractor.selectTrack(index)
            muxer = MediaMuxer(target.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
            val track = muxer.addTrack(extractor.getTrackFormat(index))
            muxer.start()
            started = true
            val buffer = ByteBuffer.allocateDirect(2 * 1024 * 1024)
            val info = MediaCodec.BufferInfo()
            while (true) {
                check()
                buffer.clear()
                val read = extractor.readSampleData(buffer, 0)
                if (read < 0) break
                require(read <= buffer.capacity()) { "Аудиосэмпл слишком большой для буфера" }
                info.set(0, read, extractor.sampleTime.coerceAtLeast(0), extractor.sampleFlags)
                muxer.writeSampleData(track, buffer, info)
                extractor.advance()
            }
        } catch (e: Exception) {
            target.delete()
            throw e
        } finally {
            try { if (started) muxer?.stop() } catch (_: Exception) {}
            muxer?.release()
            extractor.release()
            afd.close()
        }
    }

    private class Resampler(val sampleRate: Int, val channels: Int, val wav: WavSink) {
        private var inputFrame = 0L
        private var nextOutputAt = 0.0
        private var previous = 0f
        private val increment = sampleRate.toDouble() / TARGET_RATE

        fun consume(data: ByteBuffer, encoding: Int) {
            val bytesPerChannel = when (encoding) {
                AudioFormat.ENCODING_PCM_16BIT -> 2
                AudioFormat.ENCODING_PCM_FLOAT -> 4
                else -> error("Неподдерживаемый PCM-формат $encoding")
            }
            val sampleBytes = bytesPerChannel * channels
            while (data.remaining() >= sampleBytes) {
                var sum = 0f
                repeat(channels) {
                    sum += if (bytesPerChannel == 2) data.short.toFloat() / 32768f else data.float
                }
                push(sum / channels)
            }
        }

        private fun push(now: Float) {
            val n = inputFrame.toDouble()
            if (inputFrame == 0L) {
                wav.put(now)
                nextOutputAt = increment
            } else {
                while (nextOutputAt <= n) {
                    val fraction = (nextOutputAt - (n - 1)).toFloat().coerceIn(0f, 1f)
                    wav.put(previous + (now - previous) * fraction)
                    nextOutputAt += increment
                }
            }
            previous = now
            inputFrame++
        }
    }

    private class WavSink(file: File) : Closeable {
        private val raf = RandomAccessFile(file, "rw")
        private var samples = 0L
        private val bytes = ByteArray(32768)
        private var used = 0
        init { raf.setLength(0); raf.write(ByteArray(44)) }

        fun put(sample: Float) {
            val v = (sample.coerceIn(-1f, 1f) * 32767f).roundToInt()
            bytes[used++] = (v and 255).toByte()
            bytes[used++] = ((v shr 8) and 255).toByte()
            samples++
            if (used >= bytes.size) flush()
        }
        private fun flush() {
            if (used > 0) raf.write(bytes, 0, used)
            used = 0
        }
        override fun close() {
            flush()
            val dataBytes = samples * 2
            require(dataBytes < Int.MAX_VALUE.toLong() - 44) { "WAV превышает 2 ГБ" }
            raf.seek(0)
            fun fourCC(s: String) = raf.write(s.toByteArray(Charsets.US_ASCII))
            fun little16(i: Int) { raf.write(i and 255); raf.write((i shr 8) and 255) }
            fun little32(i: Int) { little16(i); little16(i shr 16) }
            fourCC("RIFF"); little32(36 + dataBytes.toInt()); fourCC("WAVE")
            fourCC("fmt "); little32(16); little16(1); little16(1)
            little32(TARGET_RATE); little32(TARGET_RATE * 2); little16(2); little16(16)
            fourCC("data"); little32(dataBytes.toInt())
            raf.close()
        }
    }
}
