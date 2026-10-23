"""Parallel transcription scheduler, adaptive CPU load and wall-clock ETA."""
from __future__ import annotations

import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED


def worker_settings(device: str, performance: str = "balanced",
                    requested: int = 0, reserve: int = 20) -> tuple[int, int]:
    """(workers, CTranslate2 cpu_threads); GPU also needs a small CPU budget."""
    if performance not in ("balanced", "quiet", "fast"):
        raise ValueError("Неизвестный режим производительности.")
    if not isinstance(reserve, int) or not 15 <= reserve <= 25:
        raise ValueError("Запас ресурсов должен быть от 15 до 25%.")
    if not isinstance(requested, int) or not 0 <= requested <= 4:
        raise ValueError("Число обработчиков должно быть от 1 до 4, либо автоматически.")
    logical = os.cpu_count() or 4
    preferred = 1 if performance == "quiet" else (3 if performance == "fast" else 2)
    workers = requested or preferred
    workers = min(workers, 4)
    if device == "cpu":
        # Leave spare logical CPUs for the desktop; adjust when background load rises.
        budget = max(1, int(logical * (100 - (reserve if performance != "fast" else 0)) / 100))
        threads = max(1, budget // workers)
    else:
        threads = 2
    return workers, threads


def cpu_load() -> float | None:
    """psutil is optional at runtime; without it fixed bounded concurrency remains."""
    try:
        import psutil
        return float(psutil.cpu_percent(interval=0.05))
    except (ImportError, OSError, RuntimeError):
        return None


def target_parallelism(maximum: int, performance: str, reserve: int,
                       load: float | None, device: str) -> int:
    if performance == "fast" or load is None:
        return maximum
    target = (100 - reserve) if performance == "balanced" else 50
    # Launch no extra work while the system is already busy. Started
    # tasks are not interrupted; lower process priority helps desktop use.
    if load >= target:
        return 1
    if load >= target - 10:
        return max(1, maximum - 1)
    return maximum


class ThroughputETA:
    """Estimate remaining wall time from newly finished audio, not cached blocks."""

    def __init__(self, initial_processed: float, duration: float, now=None):
        self.duration = duration
        self.initial_processed = initial_processed
        self.started = time.monotonic() if now is None else now
        self.samples: list[tuple[float, float]] = []
        self.newly_done = 0.

    def record(self, audio_seconds: float, total_done: float, now=None) -> float | None:
        now = time.monotonic() if now is None else now
        if audio_seconds <= 0 or self.duration <= 0:
            return None
        self.newly_done += audio_seconds
        self.samples.append((now, self.newly_done))
        self.samples = self.samples[-9:]
        anchor_time = self.started
        anchor_done = 0.
        if len(self.samples) >= 4:
            # Recent rolling throughput reacts when workers are throttled.
            anchor_time, anchor_done = self.samples[0]
        dt = max(0.01, now - anchor_time)
        delta_audio = self.newly_done - anchor_done
        speed = delta_audio / dt
        if speed <= 0.00001:
            return None
        remaining_audio = max(0., self.duration - total_done)
        return remaining_audio / speed


def run_parallel(blocks, count, process_block, on_complete,
                 performance="balanced", reserve=20, device="cpu",
                 sample_load=cpu_load):
    """Submit bounded jobs and commit on the caller's thread, one at a time.

    completed blocks are removed from the queue by the caller before entry.
    No partial chunk is committed; a crash causes its reprocessing only.
    """
    pending = list(blocks)
    in_flight = {}
    if not pending:
        return
    with ThreadPoolExecutor(max_workers=count, thread_name_prefix="ats-whisper") as executor:
        while pending or in_flight:
            load = sample_load() if performance != "fast" else None
            limit = target_parallelism(count, performance, reserve, load, device)
            while pending and len(in_flight) < limit:
                start, end = pending.pop(0)
                future = executor.submit(process_block, start, end)
                in_flight[future] = (start, end)
            if not in_flight:
                # Prevent busy looping if scheduling policy is later tightened.
                time.sleep(0.1)
                continue
            finished, _ = wait(in_flight, timeout=2., return_when=FIRST_COMPLETED)
            for future in finished:
                start, end = in_flight.pop(future)
                result = future.result()
                on_complete(start, end, result)
