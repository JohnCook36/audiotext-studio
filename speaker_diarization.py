"""Optional local speaker diarization for AudioText Studio (not voice separation).

The Windows EXE remains lightweight.  Pyannote and PyTorch are installed into a
separate per-user Python 3.12 virtual environment only when requested.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from resume_state import atomic_write_bytes, format_ms
from backend import event, find_ffmpeg, unique_path, timecode

MODEL = "pyannote/speaker-diarization-community-1"
STAMP = re.compile(r"^\[(\d+:\d\d:\d\d\.\d{3}) — (\d+:\d\d:\d\d\.\d{3})\]\s*(.+)$")
SP_NAME = re.compile(r"^Говорящий \d+$")


def seconds_from_code(code: str) -> int:
    hh, mm, sec = code.split(":")
    whole, frac = sec.split(".")
    return ((int(hh) * 60 + int(mm)) * 60 + int(whole)) * 1000 + int(frac)


def parse_transcript(path: Path) -> list[list]:
    """Read only our own timestamped Whisper TXT; no ASR rerun needed."""
    if not path.is_file() or path.suffix.lower() != ".txt":
        raise ValueError("Укажите существующий TXT-файл расшифровки AudioText Studio.")
    result = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = STAMP.match(line)
        if match:
            a, b = seconds_from_code(match.group(1)), seconds_from_code(match.group(2))
            if b > a:
                result.append([a, b, match.group(3).strip()])
    if not result:
        raise ValueError("Не найдены реплики с временными метками AudioText Studio в TXT.")
    return result


def validate_speaker_settings(job: dict) -> None:
    count = int(job.get("speaker_count", 0))
    if count < 0 or count > 12 or count == 1:
        raise ValueError("Количество голосов: авто (0) или от 2 до 12.")
    if job.get("speaker_device", "cpu") not in ("cpu", "cuda"):
        raise ValueError("Для голосов выберите CPU или NVIDIA GPU.")
    token = job.get("hf_token", "")
    if not isinstance(token, str) or len(token) > 512:
        raise ValueError("Неверный токен Hugging Face.")


def environment_dir() -> Path:
    home = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(home) / "AudioTextStudio" / "speaker-env"


def environment_python() -> Path:
    folder = environment_dir()
    return folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def external_runtime_script() -> Path:
    # PyInstaller installs the helper as data, not into the core Python bundle.
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    script = base / "speaker_runtime.py"
    if not script.is_file():
        raise RuntimeError("Не найден дополнительный модуль speaker_runtime.py. Переустановите AudioText Studio.")
    return script


def find_python() -> list[str]:
    """Prefer official Windows Python launcher with exactly supported Python 3.12."""
    choices = ([["py", "-3.12"], ["python"], ["python3"]] if os.name == "nt"
               else [["python3.12"], ["python3"], ["python"]])
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    for cmd in choices:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            probe = subprocess.run(cmd + ["-c", "import sys; print(sys.version_info[:2])"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=flags, timeout=10)
            if probe.returncode == 0 and b"(3, 12)" in probe.stdout:
                return cmd
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise RuntimeError(
        "Для дополнительного модуля голосов нужен Python 3.12 (64-bit), "
        "доступный через команду py -3.12. Установите Python 3.12 с python.org, "
        "затем снова запустите определение голосов. Обычная расшифровка доступна без него."
    )


def run_console(command: list[str], *, env: dict | None = None, max_seconds=2400) -> None:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace",
                                   creationflags=flags, env=env)
    except OSError as exc:
        raise RuntimeError("Не удалось запустить установку модуля голосов: " + str(exc)) from exc
    last = []
    started = time.monotonic()
    last_fraction = 0.
    try:
        assert process.stdout is not None
        for line in process.stdout:
            message = line.strip()
            if message:
                if message.startswith("@@ATS_SPEAKER_PROGRESS@@"):
                    try:
                        data = json.loads(message.split("@@ATS_SPEAKER_PROGRESS@@", 1)[1])
                        fraction = max(last_fraction, min(0.95, float(data["fraction"])))
                        if fraction > last_fraction:
                            last_fraction = fraction
                            event("progress", value=round(85 + 14 * fraction))
                            elapsed = time.monotonic() - started
                            # Rough estimate; includes model loading. As model stages
                            # change, remaining time may differ substantially.
                            remaining = elapsed / fraction * (1. - fraction) if fraction > 0.02 else None
                            event("eta", remaining_seconds=round(remaining)
                                  if remaining is not None else None, workers=1,
                                  phase="speakers")
                    except (ValueError, KeyError, TypeError):
                        pass
                    continue
                last.append(message)
                last = last[-8:]
                # Do not print Hugging Face token: it is passed only in the child environment.
                if not any(secret in message for secret in ("hf_", "Bearer ", "token=")):
                    event("log", message=message[:350])
            if time.monotonic() - started > max_seconds:
                process.kill()
                raise RuntimeError("Установка модуля заняла слишком много времени.")
        returncode = process.wait(timeout=10)
        if returncode:
            raise RuntimeError("Не удалось установить модуль голосов (код " +
                               str(returncode) + "). Проверьте интернет и свободное место. " +
                               "См. журнал обработки.")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def ensure_runtime() -> Path:
    python = environment_python()
    marker = environment_dir() / "installed-v1.txt"
    if python.is_file() and marker.is_file():
        return python
    parent = environment_dir()
    parent.parent.mkdir(parents=True, exist_ok=True)
    event("status", message="Подготовка дополнительного модуля голосов…")
    event("log", message="PyTorch и pyannote устанавливаются отдельно; потребуется место на диске и интернет.")
    if not python.is_file():
        run_console(find_python() + ["-m", "venv", str(parent)])
    run_console([str(python), "-m", "pip", "install", "--no-input", "--disable-pip-version-check",
                 "pyannote.audio>=4,<5"], max_seconds=3600)
    # Marker is written after successful installation only.
    marker.write_text("pyannote.audio>=4,<5\n", encoding="utf-8")
    return python


def decode_mono_wav(source: Path, wav: Path):
    """Decode the same original file as Whisper, to 16kHz PCM WAV for pyannote."""
    from backend import find_ffmpeg
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    command = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin",
               "-i", str(source), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
               "-c:a", "pcm_s16le", "-y", str(wav)]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=flags, timeout=3600)
    if result.returncode or not wav.exists() or wav.stat().st_size <= 44:
        raise RuntimeError("Не удалось подготовить WAV для анализа голосов: " +
                           result.stderr.decode("utf-8", errors="replace")[-300:])


def align_speakers(segments: list[list], turns: list[list]) -> tuple[list[list], dict]:
    """Assign each Whisper segment to the speaker with the largest overlap.

    We deliberately do not invent an identity when no overlap was detected.
    Labels are globally stable because turns are produced over the WHOLE audio.
    """
    sanitized = sorted(
        [(int(a), int(b), str(speaker)) for a, b, speaker in turns if int(b) > int(a)],
        key=lambda item: (item[0], item[1])
    )
    names = {}
    mapped = []
    for a, b, words in segments:
        overlap = {}
        for start, end, identity in sanitized:
            if start >= b:
                break
            if end <= a:
                continue
            overlap[identity] = overlap.get(identity, 0) + min(b, end) - max(a, start)
        best = max(overlap, key=overlap.get) if overlap else None
        if best is not None and best not in names:
            names[best] = f"Говорящий {len(names) + 1}"
        mapped.append([a, b, words, best])
    # Use first actual turn for any speaker with no Whisper segments.
    for _, _, identity in sanitized:
        if identity not in names:
            names[identity] = f"Говорящий {len(names) + 1}"
    return mapped, names


def render_speakers(document: dict, output: Path) -> tuple[Path, Path | None]:
    """Regenerate exports from sidecar; renaming never reruns audio analysis."""
    txt = output / document["txt_name"]
    srt = output / document["srt_name"] if document.get("srt_name") else None
    speaker_names = document["speaker_names"]
    rows = document["segments"]
    lines = [f'Источник: {document["source_name"]}\n',
             'Диаризация: pyannote Community-1\n\n']
    individual = {}
    srt_lines = []
    for i, (a, b, words, identity) in enumerate(rows, 1):
        name = speaker_names.get(identity, "Говорящий не определён") if identity else "Говорящий не определён"
        lines.append(f"[{format_ms(a)} — {format_ms(b)}] {name}: {words}\n")
        srt_lines.append(f"{i}\n{format_ms(a, True)} --> {format_ms(b, True)}\n{name}: {words}\n\n")
        individual.setdefault(name, []).append(f"[{format_ms(a)} — {format_ms(b)}] {words}\n")
    atomic_write_bytes(txt, ("\ufeff" + "".join(lines)).encode("utf-8"))
    if srt:
        atomic_write_bytes(srt, ("\ufeff" + "".join(srt_lines)).encode("utf-8"))
    return txt, srt


def save_speaker_document(path: Path, document: dict):
    atomic_write_bytes(path, json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))


def rename_speakers(document_path: Path, new_names: dict) -> dict:
    document = json.loads(document_path.read_text(encoding="utf-8"))
    output = document_path.resolve().parent
    if not isinstance(new_names, dict) or not isinstance(document.get("speaker_names"), dict):
        raise ValueError("Повреждённый файл данных голосов.")
    for identity, title in new_names.items():
        if identity not in document["speaker_names"]:
            raise ValueError("Неизвестный голос: " + str(identity))
        title = str(title).strip()
        if not title or len(title) > 60 or "\n" in title or "\r" in title:
            raise ValueError("Имя голоса должно содержать от 1 до 60 символов.")
        document["speaker_names"][identity] = title
    for filename, suffix in [(document["txt_name"], ".txt"),
                              (document.get("srt_name"), ".srt")]:
        if filename is not None and (Path(filename).name != filename or not filename.endswith(suffix)):
            raise ValueError("Небезопасный путь результата.")
    save_speaker_document(document_path, document)
    render_speakers(document, output)
    return document


def run_diarization(source: Path, transcript: Path, output: Path, job: dict) -> tuple[Path, Path | None]:
    validate_speaker_settings(job)
    segments = parse_transcript(transcript)
    out_txt = unique_path(output / (transcript.stem + "_speakers.txt"))
    out_srt = unique_path(output / (transcript.stem + "_speakers.srt")) if job.get("srt", True) else None
    event("status", message="Определение голосов по всей записи…")
    event("eta", remaining_seconds=None, workers=1, phase="speakers")
    speaker_count = int(job.get("speaker_count", 0))
    device = job.get("speaker_device", "cpu")
    st = source.stat()
    cache_key = json.dumps({"source": str(source.resolve()), "size": st.st_size,
                            "mtime_ns": st.st_mtime_ns, "model": MODEL,
                            "speaker_count": speaker_count}, sort_keys=True)
    cache_name = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:24] + "-turns.json"
    answer = output / ".audiotext-checkpoints" / cache_name
    if answer.is_file():
        event("log", message="Найден сохранённый анализ голосов — повторный запуск модели не нужен.")
    else:
        python = ensure_runtime()
        with tempfile.TemporaryDirectory(prefix="ats-speakers-") as temporary:
            wav = Path(temporary) / "mono_16k.wav"
            event("log", message="Подготовка WAV для анализа голосов (16 кГц, моно)…")
            decode_mono_wav(source, wav)
            answer.parent.mkdir(parents=True, exist_ok=True)
            command = [str(python), str(external_runtime_script()), str(wav), str(answer),
                       device, str(speaker_count), job.get("performance_mode", "balanced"),
                       str(job.get("resource_reserve", 20))]
            env = os.environ.copy()
            env["ATS_HF_TOKEN"] = (os.environ.get("ATS_HF_TOKEN") or
                                   os.environ.get("HF_TOKEN", ""))
            event("status", message="Поиск и сопоставление голосов — отдельный этап…")
            try:
                run_console(command, env=env, max_seconds=24 * 3600)
            except RuntimeError as exc:
                raise RuntimeError(str(exc) +
                                   " Если модель не загружается, примите её условия на "
                                   "huggingface.co/pyannote/speaker-diarization-community-1 "
                                   "и укажите токен Hugging Face в настройках.") from exc
    if not answer.is_file():
        raise RuntimeError("Модуль определения голосов не вернул результат.")
    if source.stat().st_size != st.st_size or source.stat().st_mtime_ns != st.st_mtime_ns:
        raise RuntimeError("Исходный файл изменился во время анализа голосов.")
    turns = json.loads(answer.read_text(encoding="utf-8"))
    if not isinstance(turns, list):
        raise RuntimeError("Некорректный ответ модуля определения голосов.")
    aligned, speaker_names = align_speakers(segments, turns)
    document = {
        "schema": 1, "source_name": source.name, "source_path": str(source.resolve()),
        "transcript_name": transcript.name, "txt_name": out_txt.name,
        "srt_name": out_srt.name if out_srt else None,
        "speaker_names": speaker_names, "segments": aligned
    }
    metadata = output / (out_txt.stem + ".json")
    # Sidecar is authoritative: if export is interrupted, it can be re-rendered.
    save_speaker_document(metadata, document)
    render_speakers(document, output)
    event("log", message=f"Обнаружено голосов: {len(speaker_names)}")
    event("log", message="Размеченная расшифровка: " + out_txt.name)
    if out_srt:
        event("log", message="Субтитры по говорящим: " + out_srt.name)
    event("log", message="Файл для переименования голосов: " + metadata.name)
    return out_txt, out_srt
