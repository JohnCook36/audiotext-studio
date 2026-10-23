"""Run pyannote Community-1 inside the optional Python 3.12 environment.

No cloud inference: Hugging Face is only contacted to download model weights.
The user must accept the model conditions and supply a read token once.
"""
from __future__ import annotations
import json
import os
import sys
import wave
from pathlib import Path


def read_waveform(path: Path):
    import numpy as np
    import torch
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getframerate() != 16000 or wav.getsampwidth() != 2:
            raise RuntimeError("Входная дорожка должна быть моно PCM16 WAV 16 кГц.")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    if samples.size == 0:
        raise RuntimeError("В аудио нет звуковых данных.")
    return torch.from_numpy(samples.astype("float32") / 32768.0).unsqueeze(0)


def main() -> int:
    if len(sys.argv) != 7:
        raise SystemExit("Usage: speaker_runtime.py input.wav turns.json cpu|cuda speaker_count performance reserve")
    source, result_path, device, count, performance, reserve = sys.argv[1:]
    count, reserve = int(count), int(reserve)
    import torch
    from pyannote.audio import Pipeline

    available = os.cpu_count() or 4
    threads = max(1, int(available * ((100-reserve)/100))) if performance == "balanced" else (
        max(1, available // 2) if performance == "quiet" else available
    )
    torch.set_num_threads(min(threads, 16))
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("Установленная версия PyTorch не поддерживает CUDA или драйвер не готов. "
                           "Выберите CPU для определения голосов.")

    token = os.environ.get("ATS_HF_TOKEN") or None
    # Never print or persist the Hugging Face access token.
    print("Загрузка локальной модели pyannote Community-1 (при первом использовании требуется интернет)…", flush=True)
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=token)
    if pipeline is None:
        raise RuntimeError("Модель недоступна. Примите условия использования в Hugging Face.")
    if device == "cuda":
        pipeline.to(torch.device("cuda"))
    waveform = read_waveform(Path(source))
    print("Анализ уникальных голосов по всей записи…", flush=True)
    kwargs = {"num_speakers":count} if count else {}
    last_report = {}

    def progress_hook(step, artifact=None, *, completed=None, total=None, **unused):
        if not total or completed is None:
            return
        portion = max(0.0, min(1.0, float(completed) / float(total)))
        key = str(step).lower()
        # Pipeline phases consume different amounts of time; these are rough
        # progress weights rather than an exact per-frame completion metric.
        if "segment" in key:
            fraction = portion * 0.60
        elif "embed" in key:
            fraction = 0.60 + portion * 0.35
        else:
            return
        previous = last_report.get(key, -1.0)
        if fraction - previous >= 0.015 or portion >= 1:
            last_report[key] = fraction
            print("@@ATS_SPEAKER_PROGRESS@@" +
                  json.dumps({"step":key,"fraction":fraction}), flush=True)

    with torch.inference_mode():
        output = pipeline({"waveform": waveform, "sample_rate":16000},
                          hook=progress_hook, **kwargs)
    annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(output, "speaker_diarization", None)
    if annotation is None:
        annotation = output
    turns = []
    # Annotation.itertracks(yield_label=True) is supported by pyannote.core 4.x.
    for segment, _, speaker in annotation.itertracks(yield_label=True):
        a, b = round(float(segment.start)*1000), round(float(segment.end)*1000)
        if b > a:
            turns.append([a,b,str(speaker)])
    target = Path(result_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(turns, file, ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, target)
    print(f"Анализ голосов завершён, речевых интервалов: {len(turns)}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Avoid leaking secrets from model-related exception messages.
        print("ОШИБКА: Не удалось проанализировать голоса. Проверьте установку, "
              "доступ к модели Hugging Face и память компьютера. "
              "Технический тип ошибки: " + type(exc).__name__, flush=True)
        raise SystemExit(1)
