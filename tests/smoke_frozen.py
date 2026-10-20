"""Integration smoke-test against Windows PyInstaller --windowed executable.

The GUI executable starts the worker when called with --worker <config.json>.
This test verifies event IPC and FFmpeg extraction without fetching a Whisper model.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg


def run(*command: str, **kwargs):
    p = subprocess.run(command, capture_output=True, text=True, timeout=90, **kwargs)
    if p.returncode:
        raise RuntimeError(f'Command failed ({p.returncode}): {command!r}\n{p.stderr}')
    return p


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit('Usage: smoke_frozen.py <path-to-AudioTextStudio.exe>')
    exe = Path(sys.argv[1]).resolve()
    if not exe.is_file():
        raise SystemExit(f'Executable not found: {exe}')

    with tempfile.TemporaryDirectory(prefix='ats-frozen-') as temp:
        folder = Path(temp)
        video = folder / 'Проверка видео.mp4'
        output = folder / 'Результаты'
        event_file = folder / 'events.jsonl'
        job_file = folder / 'job.json'
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        run(ffmpeg, '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=1',
            '-f', 'lavfi', '-i', 'color=black:s=128x128:r=10:d=1',
            '-map', '1:v:0', '-map', '0:a:0',
            '-c:v', 'mpeg4', '-c:a', 'aac', '-shortest', '-y', str(video))
        job_file.write_text(json.dumps({
            'file': str(video), 'output': str(output), 'mode': 'extract',
            'audio_format': 'mp3', 'model': 'small', 'language': 'ru',
        }, ensure_ascii=False), encoding='utf-8')
        event_file.touch()
        env = dict(os.environ, ATS_EVENT_FILE=str(event_file))
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        result = subprocess.run([str(exe), '--worker', str(job_file)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=env, creationflags=creationflags, timeout=90)
        events = [json.loads(line) for line in event_file.read_text(encoding='utf-8').splitlines() if line]
        mp3s = list(output.glob('*_audio.mp3'))
        if result.returncode != 0 or not mp3s or mp3s[0].stat().st_size < 100:
            raise RuntimeError(f'Packaged worker failed: rc={result.returncode}; events={events}')
        if not any(e['type'] == 'done' for e in events):
            raise RuntimeError(f'No done event after extraction: {events}')
    print('OK: packaged Windows worker extracted MP3 and reported success via IPC')


if __name__ == '__main__':
    main()