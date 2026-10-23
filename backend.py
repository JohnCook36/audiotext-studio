# -*- coding: utf-8 -*-
"""Локальные задачи извлечения аудио и распознавания речи.

Работает в отдельном процессе; взаимодействие с GUI — JSON-события через stdout.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

VIDEO = {'.mp4', '.mkv', '.mov', '.avi', '.webm', '.wmv', '.m4v', '.flv', '.mpeg', '.mpg', '.3gp', '.ts', '.mts', '.m2ts'}
AUDIO = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.wma', '.aiff', '.aif', '.amr', '.mp4a'}
FORMATS = ('mp3', 'm4a', 'wav')
MODELS = ('tiny', 'base', 'small', 'medium', 'large-v3')
DEVICES = ('cpu', 'cuda')


def event(kind: str, **data):
    """Отправляет события через файл IPC (работает и в PyInstaller windowed EXE).

    В windowed-сборках sys.stdout может быть None; поэтому GUI читает
    события из временного файла, указанного в ATS_EVENT_FILE.
    """
    line = json.dumps({'type': kind, **data}, ensure_ascii=False) + '\n'
    event_file = os.environ.get('ATS_EVENT_FILE')
    if event_file:
        with open(event_file, 'a', encoding='utf-8') as out:
            out.write(line)
        return
    if sys.stdout is not None:
        print('@@EVENT@@' + line.rstrip('\n'), flush=True)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while True:
        candidate = path.with_name(f'{path.stem} ({i}){path.suffix}')
        if not candidate.exists():
            return candidate
        i += 1


def timecode(seconds: float, srt: bool = False) -> str:
    total = max(0, round(seconds * 1000))
    hh, remain = divmod(total, 3_600_000)
    mm, remain = divmod(remain, 60_000)
    ss, msec = divmod(remain, 1000)
    return f'{hh:02}:{mm:02}:{ss:02}{"," if srt else "."}{msec:03}'


def parse_ffmpeg_timestamp(value: str) -> float | None:
    """Парсит out_time=HH:MM:SS.mmm; out_time_ms может быть в микросекундах."""
    found = re.fullmatch(r'(\d+):(\d\d):(\d\d)(?:\.(\d+))?', value.strip())
    if not found:
        return None
    hours, mins, sec, frac = found.groups()
    return int(hours) * 3600 + int(mins) * 60 + int(sec) + float('0.' + (frac or '0'))


def find_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        path = shutil.which('ffmpeg')
        if path:
            return path
    raise RuntimeError('FFmpeg не найден в установке. Переустановите приложение.')


def media_duration(source: Path) -> float | None:
    try:
        import av
        with av.open(str(source)) as container:
            if container.duration:
                return float(container.duration / 1_000_000)
            for stream in container.streams.audio:
                if stream.duration and stream.time_base:
                    return float(stream.duration * stream.time_base)
    except Exception:
        pass
    return None


def validate_job(job: dict) -> tuple[Path, Path, bool, str]:
    source = Path(job['file']).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f'Файл не найден: {source}')
    if source.suffix.lower() not in VIDEO | AUDIO:
        raise ValueError(f'Неподдерживаемое расширение: {source.suffix}')
    is_video = source.suffix.lower() in VIDEO
    mode = job.get('mode', 'both')
    if mode not in ('extract', 'transcribe', 'both'):
        raise ValueError('Неверный режим обработки.')
    if not is_video and mode != 'transcribe':
        raise ValueError('Для аудиофайлов доступно только распознавание речи.')
    if job.get('audio_format', 'mp3') not in FORMATS:
        raise ValueError('Неизвестный формат аудио.')
    if job.get('model', 'small') not in MODELS:
        raise ValueError('Неизвестная модель распознавания.')
    if job.get('device', 'cpu') not in DEVICES:
        raise ValueError('Неизвестное устройство распознавания: выберите CPU или GPU.')
    from parallel_engine import worker_settings
    worker_settings(job.get('device', 'cpu'), job.get('performance_mode', 'balanced'),
                    job.get('worker_count', 0), job.get('resource_reserve', 20))
    output_text = job.get('output', '').strip()
    output = Path(output_text).expanduser().resolve() if output_text else source.parent / 'Результаты_распознавания'
    return source, output, is_video, mode


def extract_audio(source: Path, output: Path, audio_format: str, duration: float | None) -> Path:
    codecs = {
        'mp3': ['-c:a', 'libmp3lame', '-q:a', '3'],
        'm4a': ['-c:a', 'aac', '-b:a', '160k'],
        'wav': ['-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le'],
    }
    dest = unique_path(output / (source.stem + '_audio.' + audio_format))
    event('status', message='Извлечение аудио из видео…')
    cmd = [find_ffmpeg(), '-hide_banner', '-nostdin', '-loglevel', 'error',
           '-progress', 'pipe:1', '-i', str(source), '-map', '0:a:0', '-vn',
           *codecs[audio_format], '-y', str(dest)]
    errors = []
    try:
        # FFmpeg is a console executable. A windowed Windows build must launch it
        # without creating a separate terminal window while preserving pipe progress.
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True, encoding='utf-8', errors='replace',
                                   creationflags=flags)
        assert process.stdout is not None
        for line in process.stdout:
            s = line.strip()
            if s.startswith('out_time=') and duration and duration > 0:
                seconds = parse_ffmpeg_timestamp(s.split('=', 1)[1])
                if seconds is not None:
                    event('progress', value=min(42, max(0, int(seconds / duration * 42))))
            elif s and '=' not in s:
                errors.append(s)
        result = process.wait()
    except OSError as exc:
        raise RuntimeError(f'Не удалось запустить FFmpeg: {exc}') from exc
    if result != 0 or not dest.exists() or dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError('Не удалось извлечь звук. У видео может отсутствовать аудиодорожка. '\
                           + ' '.join(errors[-3:]))
    event('log', message='Аудиодорожка сохранена: ' + dest.name)
    return dest


def whisper_device_options(device: str) -> dict:
    """Выбор CPU int8 или CUDA float16 без автоматической подмены устройства."""
    if device == 'cpu':
        return {'device': 'cpu', 'compute_type': 'int8'}
    if device == 'cuda':
        return {'device': 'cuda', 'compute_type': 'float16'}
    raise ValueError('Неподдерживаемое устройство Whisper: ' + str(device))


def check_cuda_available():
    """Проверка драйвера и GPU перед долгим извлечением аудио."""
    try:
        import ctranslate2
        count = ctranslate2.get_cuda_device_count()
    except (RuntimeError, OSError, ImportError) as exc:
        raise RuntimeError(
            'Не удалось определить NVIDIA GPU. Проверьте драйвер NVIDIA и CUDA 12. '
            'Причина: ' + str(exc)
        ) from exc
    if count < 1:
        raise RuntimeError('NVIDIA GPU с поддержкой CUDA не найдена. Выберите CPU или настройте драйвер NVIDIA.')


def missing_cuda_runtime_dlls(search_dirs) -> list[str]:
    """Only check directories visible to the app's DLL loader, not random downloads."""
    names = ('cublas64_12.dll', 'cudnn64_9.dll')
    paths = [Path(d) for d in search_dirs if d]
    return [name for name in names if not any((folder / name).is_file() for folder in paths)]


def check_cuda_runtime_dlls():
    """Fail early rather than extracting a long video before a missing-DLL error."""
    if sys.platform != 'win32':
        return
    root = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    dirs = [root, Path(sys.executable).parent]
    dirs.extend(p.strip('"') for p in os.environ.get('PATH', '').split(os.pathsep) if p.strip())
    missing = missing_cuda_runtime_dlls(dirs)
    if missing:
        raise RuntimeError(
            'Для NVIDIA GPU недоступны библиотеки: ' + ', '.join(missing) + '. '
            'Установите CUDA Toolkit 12 (cuBLAS) и cuDNN 9 с официального сайта NVIDIA. '
            'Каталоги с DLL должны присутствовать в системной переменной PATH. '
            'После установки перезапустите AudioText Studio. '
            'Или выберите CPU в настройках.'
        )


def create_whisper_model(name: str, device: str, workers: int = 1,
                         cpu_threads: int | None = None):
    options = whisper_device_options(device)
    if workers > 1:
        options['num_workers'] = workers
    if cpu_threads is not None:
        options['cpu_threads'] = cpu_threads
    if device == 'cuda':
        check_cuda_available()
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(name, **options)
    except Exception as exc:
        if device == 'cuda':
            raise RuntimeError(
                'Whisper не удалось запустить на NVIDIA GPU. '
                'Нужны CUDA 12 (cuBLAS) и cuDNN 9, доступные приложению, '
                'а также актуальный драйвер NVIDIA. Можно выбрать CPU. '
                'Причина: ' + str(exc)
            ) from exc
        raise


def decode_transcription_chunk(source: Path, start: float, end: float):
    """Decode only a small window; avoids loading hours of audio into RAM."""
    import numpy as np

    cmd = [find_ffmpeg(), '-hide_banner', '-nostdin', '-loglevel', 'error',
           '-ss', f'{start:.3f}', '-i', str(source), '-t', f'{max(0.1, end - start):.3f}',
           '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000',
           '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1']
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=flags, timeout=180)
    if result.returncode != 0:
        reason = result.stderr.decode('utf-8', errors='replace')[-700:]
        raise RuntimeError('Ошибка чтения фрагмента аудио через FFmpeg: ' + reason)
    return np.frombuffer(result.stdout, dtype='<f4').copy()


def begin_resume_job(job: dict, source: Path, output: Path):
    """Prepare a deterministic checkpoint before video extraction or inference."""
    from resume_state import (
        checkpoint_path, load_checkpoint, settings_identity,
        save_checkpoint, render_transcripts,
    )

    path = checkpoint_path(job, source, output)
    policy = job.get('resume_policy', 'auto')
    if policy not in ('auto', 'resume', 'restart'):
        raise ValueError('Неизвестный режим возобновления.')
    state = load_checkpoint(job, source, output)
    if policy == 'restart' and state:
        path.unlink()
        state = None
        event('log', message='Начата новая расшифровка. Предыдущие TXT/SRT сохранены.')
    if state is None:
        state = {
            'schema': 1,
            'identity': settings_identity(job, source, output),
            'cursor_ms': 0,
            'segments': [],
            'detected_language': None,
            'audio_name': None,
            'txt_name': unique_path(output / (source.stem + '_transcript.txt')).name,
            'srt_name': unique_path(output / (source.stem + '_subtitles.srt')).name
                        if job.get('srt', True) else None,
        }
        save_checkpoint(path, state)
        render_transcripts(state, output, source)
        event('log', message='Создана контрольная точка: ' + str(path))
    else:
        # Checkpoint is canonical: fix outputs if the machine crashed between writes.
        render_transcripts(state, output, source)
        event('log', message='Найдена контрольная точка. Продолжение с ' +
              timecode(state['cursor_ms'] / 1000) +
              ', сохранено фраз: ' + str(len(state['segments'])))
    return state, path


def transcribe(source: Path, original: Path, output: Path, job: dict, pct_start: int,
               state: dict, checkpoint: Path) -> tuple[Path, Path | None]:
    from parallel_engine import ThroughputETA, worker_settings, run_parallel
    from resume_state import (
        CHUNK_SECONDS, OVERLAP_SECONDS, save_checkpoint, render_transcripts,
        output_path, file_identity, migrate_parallel_checkpoint,
        sync_completed_blocks, completed_audio_ms,
    )
    import time

    duration = media_duration(source) or media_duration(original)
    if not duration or duration <= 0:
        raise RuntimeError('Не удалось определить длительность аудио для безопасного '
                           'параллельного распознавания. Попробуйте WAV или MP3.')
    total_ms = max(1, round(duration * 1000))
    model_name = job.get('model', 'small')
    language = job.get('language', 'ru')
    device = job.get('device', 'cpu')
    performance = job.get('performance_mode', 'balanced')
    reserve = job.get('resource_reserve', 20)
    requested = job.get('worker_count', 0)
    worker_count, cpu_threads = worker_settings(device, performance, requested, reserve)
    event('log', message=f'Параллельные обработчики: {worker_count}; '
          f'запас ресурсов: {reserve}% ({performance})')

    migrate_parallel_checkpoint(state)
    sync_completed_blocks(state)
    save_checkpoint(checkpoint, state)
    txt = output_path(output, state['txt_name'], '.txt')
    srt = output_path(output, state['srt_name'], '.srt') if state['srt_name'] else None
    base_ms = state['base_cursor_ms']
    chunks = [
        (position, min(position + CHUNK_SECONDS * 1000, total_ms))
        for position in range(base_ms, total_ms, CHUNK_SECONDS * 1000)
        if str(position) not in state['completed_chunks']
    ]
    total_done_ms = completed_audio_ms(state)
    if total_done_ms >= total_ms and not chunks:
        render_transcripts(state, output, original)
        checkpoint.unlink(missing_ok=True)
        event('log', message='Все блоки уже были распознаны, результаты восстановлены.')
        return txt, srt

    event('status', message='Подготовка модели ' + model_name + '…')
    event('log', message='Устройство: ' +
          ('NVIDIA GPU (CUDA / float16)' if device == 'cuda' else 'CPU (int8)'))
    model = create_whisper_model(model_name, device,
                                 workers=worker_count, cpu_threads=cpu_threads)
    event('status', message='Параллельное распознавание с контрольными точками…')
    event('progress', value=min(99, int(pct_start +
                                          (100 - pct_start) * total_done_ms / total_ms)))
    event('eta', remaining_seconds=None, workers=worker_count,
          processed_audio_seconds=total_done_ms / 1000,
          total_audio_seconds=total_ms / 1000)

    expected_source = state['identity']['source']
    estimate = ThroughputETA(total_done_ms / 1000, total_ms / 1000)

    def process_block(core_start: int, core_end: int):
        if file_identity(original) != expected_source:
            raise RuntimeError('Исходный файл изменился во время обработки. '
                               'Сохранённый текст и контрольные точки остаются на диске.')
        start = max(0., core_start / 1000 - OVERLAP_SECONDS)
        end = core_end / 1000 + (OVERLAP_SECONDS if core_end < total_ms else 0)
        samples = decode_transcription_chunk(source, start, end)
        decoded_end = round(start * 1000 + len(samples) / 16)
        if not len(samples) or decoded_end + 2000 < core_end:
            raise RuntimeError('Не удалось прочитать аудио до ' +
                               timecode(core_end / 1000) + '. Проверьте файл.')
        # CTranslate2 is configured with num_workers to serve concurrent calls
        # without duplicating the model separately in every Python process.
        effective_language = state.get('detected_language') if language == 'auto' else language
        segments, info = model.transcribe(
            samples, language=effective_language,
            beam_size=5, vad_filter=bool(job.get('vad', True)),
        )
        additions = []
        for segment in segments:
            words = ' '.join(segment.text.split())
            if not words:
                continue
            a = max(0, round(start * 1000 + float(segment.start) * 1000))
            b = min(round(end * 1000), round(start * 1000 + float(segment.end) * 1000))
            midpoint = (a + b) / 2
            if b > a and core_start <= midpoint < core_end:
                additions.append([a, b, words])
        return additions, info.language

    def commit_block(core_start: int, core_end: int, result):
        if str(core_start) in state['completed_chunks']:
            return
        additions, detected_language = result
        state['completed_chunks'][str(core_start)] = {
            'end_ms': core_end,
            'segments': additions,
        }
        if language == 'auto' and not state.get('detected_language'):
            state['detected_language'] = detected_language
        contiguous = sync_completed_blocks(state)
        # Save authoritative data BEFORE regenerating human-readable output.
        # An interruption between these operations is safe to recover.
        save_checkpoint(checkpoint, state)
        render_transcripts(state, output, original)
        done_ms = completed_audio_ms(state)
        remaining = estimate.record((core_end - core_start) / 1000, done_ms / 1000)
        event('progress', value=min(99, int(pct_start +
                                          (100 - pct_start) * done_ms / total_ms)))
        event('eta', remaining_seconds=round(remaining) if remaining is not None else None,
              workers=worker_count, processed_audio_seconds=done_ms / 1000,
              total_audio_seconds=total_ms / 1000)
        event('status', message='Сохранено ' + timecode(done_ms / 1000) +
              ' из ' + timecode(total_ms / 1000) +
              f' · готовых фрагментов: {len(state["completed_chunks"])}')
        event('log', message='Контрольная точка: ' + timecode(contiguous / 1000) +
              f' · обработано {done_ms / total_ms * 100:.1f}%')

    run_parallel(chunks, worker_count, process_block, commit_block,
                 performance=performance, reserve=reserve, device=device)
    if completed_audio_ms(state) < total_ms:
        raise RuntimeError('Не все фрагменты удалось сохранить. Контрольная точка '
                           'оставлена для повторного запуска.')
    render_transcripts(state, output, original)
    checkpoint.unlink(missing_ok=True)
    event('eta', remaining_seconds=0, workers=worker_count,
          processed_audio_seconds=total_ms / 1000,
          total_audio_seconds=total_ms / 1000)
    event('log', message='Расшифровка завершена; все контрольные точки закрыты.')
    event('log', message='Текст сохранён: ' + txt.name)
    if srt:
        event('log', message='Субтитры сохранены: ' + srt.name)
    return txt, srt


def run_job(job: dict):
    source, output, is_video, mode = validate_job(job)
    output.mkdir(parents=True, exist_ok=True)
    event('status', message='Подготовка файла…')
    event('log', message='Файл: ' + source.name)
    duration = media_duration(source)
    if duration:
        event('log', message=f'Длительность: {int(duration // 60)} мин. {int(duration % 60)} сек.')
    event('progress', value=0)
    if mode != 'extract' and job.get('device', 'cpu') == 'cuda':
        event('status', message='Проверка NVIDIA GPU…')
        check_cuda_available()
        check_cuda_runtime_dlls()

    state = checkpoint = None
    if mode != 'extract':
        state, checkpoint = begin_resume_job(job, source, output)

    work = source
    if is_video and mode in ('extract', 'both'):
        from resume_state import save_checkpoint, output_path
        saved = state.get('audio_name') if state else None
        if saved and output_path(output, saved, '.' + job.get('audio_format', 'mp3')).is_file():
            work = output_path(output, saved, '.' + job.get('audio_format', 'mp3'))
            if work.stat().st_size == 0:
                work = source
            else:
                event('log', message='Повторное извлечение звука не требуется: ' + work.name)
                event('progress', value=42)
        if work == source:
            work = extract_audio(source, output, job.get('audio_format', 'mp3'), duration)
            if state is not None:
                state['audio_name'] = work.name
                save_checkpoint(checkpoint, state)

    if mode != 'extract':
        transcribe(work, source, output, job, 42 if is_video and mode == 'both' else 0,
                   state, checkpoint)
    event('progress', value=100)
    event('done', output=str(output), message='Готово. Файлы сохранены.')

def worker_main(path: str) -> int:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            job = json.load(f)
        run_job(job)
        return 0
    except Exception as exc:
        event('error', message=f'{type(exc).__name__}: {exc}')
        event('debug', message=traceback.format_exc()[-4000:])
        return 1