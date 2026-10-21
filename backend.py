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


def transcribe(source: Path, original: Path, output: Path, job: dict, pct_start: int) -> tuple[Path, Path | None]:
    # Импорт внутри worker: главное окно быстро открывается даже при больших библиотеках.
    from faster_whisper import WhisperModel

    model_name = job.get('model', 'small')
    language = job.get('language', 'ru')
    event('status', message='Подготовка модели ' + model_name + '…')
    event('log', message='При первом запуске модель скачивается из интернета. В дальнейшем работает локально.')
    model = WhisperModel(model_name, device='cpu', compute_type='int8')
    event('status', message='Распознавание речи…')
    segments, info = model.transcribe(
        str(source), language=None if language == 'auto' else language,
        beam_size=5, vad_filter=bool(job.get('vad', True)),
    )
    txt = unique_path(output / (original.stem + '_transcript.txt'))
    srt = unique_path(output / (original.stem + '_subtitles.srt')) if job.get('srt', True) else None
    event('log', message=f'Язык: {info.language}; длительность: {info.duration / 60:.1f} мин.')
    duration = max(1.0, float(info.duration or 1))
    count = 0
    # Создаём файлы и сбрасываем буфер после каждой фразы: при прерывании останется частичный текст.
    with txt.open('w', encoding='utf-8-sig') as ftxt:
        ftxt.write(f'Источник: {original.name}\nМодель: {model_name}; язык: {info.language}\n\n')
        fsrt = srt.open('w', encoding='utf-8-sig') if srt else None
        try:
            for seg in segments:
                words = ' '.join(seg.text.split())
                if not words:
                    continue
                count += 1
                ftxt.write(f'[{timecode(seg.start)} — {timecode(seg.end)}] {words}\n')
                ftxt.flush()
                if fsrt:
                    fsrt.write(f'{count}\n{timecode(seg.start, True)} --> {timecode(seg.end, True)}\n{words}\n\n')
                    fsrt.flush()
                event('progress', value=min(99, max(pct_start, int(pct_start + (100 - pct_start) * seg.end / duration))))
                if count == 1 or count % 12 == 0:
                    event('status', message=f'Распознано до {timecode(seg.end)[:8]} · {count} фрагментов')
        finally:
            if fsrt:
                fsrt.close()
    if not count:
        event('log', message='Речь не обнаружена. TXT содержит сведения о файле.')
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
    work = source
    if is_video and mode in ('extract', 'both'):
        work = extract_audio(source, output, job.get('audio_format', 'mp3'), duration)
    if mode != 'extract':
        transcribe(work, source, output, job, 42 if is_video and mode == 'both' else 0)
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