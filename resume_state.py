"""Durable per-audio-chunk transcription checkpoints.

A checkpoint is the source of truth; TXT/SRT are atomically regenerated from it.
It never contains the audio itself, and does not depend on CPU vs GPU.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

SCHEMA = 1
CHUNK_SECONDS = 60
OVERLAP_SECONDS = 2


def file_identity(source: Path) -> dict:
    st = source.stat()
    return {"path": str(source.resolve()), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def settings_identity(job: dict, source: Path, output: Path) -> dict:
    return {
        "schema": SCHEMA,
        "source": file_identity(source),
        "output": str(output.resolve()),
        "mode": job.get("mode", "both"),
        "model": job.get("model", "small"),
        "language": job.get("language", "ru"),
        "vad": bool(job.get("vad", True)),
        "audio_format": job.get("audio_format", "mp3"),
        "srt": bool(job.get("srt", True)),
        "chunk_seconds": CHUNK_SECONDS,
    }


def checkpoint_path(job: dict, source: Path, output: Path) -> Path:
    key = settings_identity(job, source, output)
    digest = hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
    return output / ".audiotext-checkpoints" / (digest + ".json")


def _checked_basename(name, output: Path, suffix: str):
    if not isinstance(name, str) or not name or Path(name).name != name or not name.lower().endswith(suffix):
        raise ValueError("Повреждённый путь в контрольной точке: " + str(name))
    return output / name


def load_checkpoint(job: dict, source: Path, output: Path) -> dict | None:
    path = checkpoint_path(job, source, output)
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        assert state["identity"] == settings_identity(job, source, output)
        assert state["schema"] == SCHEMA
        assert isinstance(state["cursor_ms"], int) and state["cursor_ms"] >= 0
        assert isinstance(state["segments"], list)
        if "completed_chunks" in state:
            assert isinstance(state["completed_chunks"], dict)
            assert isinstance(state.get("base_cursor_ms"), int)
            assert isinstance(state.get("legacy_segments"), list)
            for key, block in state["completed_chunks"].items():
                assert isinstance(key, str) and key.isdigit()
                assert isinstance(block, dict)
                assert isinstance(block["end_ms"], int) and block["end_ms"] > int(key)
                assert isinstance(block["segments"], list)
                assert all(
                    isinstance(x, list) and len(x) == 3 and
                    isinstance(x[0], int) and isinstance(x[1], int)
                    and 0 <= x[0] < x[1] and isinstance(x[2], str)
                    for x in block["segments"]
                )
        assert isinstance(state["txt_name"], str)
        _checked_basename(state["txt_name"], output, ".txt")
        if state["srt_name"] is not None:
            _checked_basename(state["srt_name"], output, ".srt")
        if state.get("audio_name") is not None:
            _checked_basename(state["audio_name"], output, "." + job.get("audio_format", "mp3"))
        assert all(
            isinstance(s, list) and len(s) == 3
            and isinstance(s[0], int) and isinstance(s[1], int)
            and 0 <= s[0] <= s[1] and isinstance(s[2], str)
            for s in state["segments"]
        )
        return state
    except (OSError, ValueError, KeyError, AssertionError, TypeError) as exc:
        raise RuntimeError(
            "Контрольная точка повреждена. Файл сохранён и не перезаписан: "
            + str(path) + ". Переместите её в другое место для начала новой расшифровки."
        ) from exc


def atomic_write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".ats-",
                                         suffix=".tmp", delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_checkpoint(path: Path, state: dict):
    data = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write_bytes(path, data)


def output_path(output: Path, name: str, suffix: str) -> Path:
    return _checked_basename(name, output, suffix)


def render_transcripts(state: dict, output: Path, original: Path):
    """Outputs can always be rebuilt after a crash, with no duplicated segments."""
    txt = output_path(output, state["txt_name"], ".txt")
    model = state["identity"]["model"]
    language = state.get("detected_language") or state["identity"]["language"]
    lines = [
        f"Источник: {original.name}\nМодель: {model}; язык: {language}\n\n"
    ]
    for start_ms, end_ms, words in state["segments"]:
        lines.append(
            f"[{format_ms(start_ms)} — {format_ms(end_ms)}] {words}\n"
        )
    atomic_write_bytes(txt, ("\ufeff" + "".join(lines)).encode("utf-8"))
    if state["srt_name"] is not None:
        subtitles = []
        for count, (start_ms, end_ms, words) in enumerate(state["segments"], 1):
            subtitles.append(
                f"{count}\n{format_ms(start_ms, srt=True)} --> "
                f"{format_ms(end_ms, srt=True)}\n{words}\n\n"
            )
        srt = output_path(output, state["srt_name"], ".srt")
        atomic_write_bytes(srt, ("\ufeff" + "".join(subtitles)).encode("utf-8"))


def format_ms(milliseconds: int, srt: bool = False) -> str:
    hh, remain = divmod(max(0, milliseconds), 3_600_000)
    mm, remain = divmod(remain, 60_000)
    ss, frac = divmod(remain, 1000)
    return f"{hh:02}:{mm:02}:{ss:02}{',' if srt else '.'}{frac:03}"


def migrate_parallel_checkpoint(state: dict) -> None:
    """Preserve a 1.2.0 checkpoint without losing its completed audio."""
    if "completed_chunks" in state:
        return
    state["base_cursor_ms"] = int(state["cursor_ms"])
    state["legacy_segments"] = list(state["segments"])
    state["completed_chunks"] = {}


def sync_completed_blocks(state: dict) -> int:
    """Rebuild ordered output, and contiguous cursor, from durable finished blocks."""
    migrate_parallel_checkpoint(state)
    saved = list(state["legacy_segments"])
    blocks = state["completed_chunks"]
    for key in sorted(blocks, key=int):
        saved.extend(blocks[key]["segments"])
    saved.sort(key=lambda x: (x[0], x[1]))
    state["segments"] = saved

    cursor = state["base_cursor_ms"]
    while str(cursor) in blocks:
        next_cursor = blocks[str(cursor)]["end_ms"]
        if next_cursor <= cursor:
            raise RuntimeError("Некорректная контрольная точка: зацикливание блоков.")
        cursor = next_cursor
    state["cursor_ms"] = cursor
    return cursor


def completed_audio_ms(state: dict) -> int:
    migrate_parallel_checkpoint(state)
    return state["base_cursor_ms"] + sum(
        x["end_ms"] - int(k) for k, x in state["completed_chunks"].items()
    )


def checkpoint_summary(job: dict, source: Path, output: Path) -> dict | None:
    """Fast lookup for GUI; it does not start Whisper or read any media data."""
    state = load_checkpoint(job, source, output)
    if state is None:
        return None
    return {
        "cursor_seconds": state["cursor_ms"] / 1000,
        "completed_audio_seconds": completed_audio_ms(state) / 1000,
        "segments": len(state["segments"]),
        "txt_name": state["txt_name"],
    }
