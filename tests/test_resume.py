"""Crash-recovery tests: no Whisper download or GPU required."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from backend import begin_resume_job, run_job, transcribe
from resume_state import (
    checkpoint_path, checkpoint_summary, load_checkpoint,
    render_transcripts, save_checkpoint, settings_identity
)


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "lecture.wav"
        self.source.write_bytes(b"Audio placeholder")
        self.output = Path(self.temp.name) / "result"
        self.output.mkdir()
        self.job = {
            "file": str(self.source), "output": str(self.output),
            "mode": "transcribe", "model": "small", "language": "ru",
            "device": "cpu", "srt": True, "vad": False,
            "audio_format": "mp3",
        }

    def test_checkpoint_identity_survives_cpu_to_gpu(self):
        initial = checkpoint_path(self.job, self.source, self.output)
        gpu_job = dict(self.job, device="cuda")
        self.assertEqual(initial, checkpoint_path(gpu_job, self.source, self.output))
        self.assertNotEqual(
            initial, checkpoint_path(dict(self.job, model="medium"), self.source, self.output))
        self.assertNotEqual(
            initial, checkpoint_path(dict(self.job, language="auto"), self.source, self.output))

    def test_changed_audio_produces_a_new_checkpoint_id(self):
        old = checkpoint_path(self.job, self.source, self.output)
        self.source.write_bytes(b"Different audio placeholder")
        self.assertNotEqual(old, checkpoint_path(self.job, self.source, self.output))

    def test_checkpoint_restores_text_after_crash_between_writes(self):
        with patch("backend.event"):
            state, path = begin_resume_job(self.job, self.source, self.output)
        state["segments"] = [[1000, 3000, "Привет"], [4000, 5000, "Мир"]]
        state["cursor_ms"] = 60000
        save_checkpoint(path, state)
        # Simulate missing or stale TXT/SRT due to power loss after checkpoint.
        (self.output / state["txt_name"]).write_text("OLD", encoding="utf-8")
        (self.output / state["srt_name"]).unlink()
        self.assertEqual(checkpoint_summary(self.job, self.source, self.output)["cursor_seconds"], 60)
        with patch("backend.event"):
            restored, _ = begin_resume_job(self.job, self.source, self.output)
        self.assertEqual(len(restored["segments"]), 2)
        txt = (self.output / state["txt_name"]).read_text(encoding="utf-8-sig")
        srt = (self.output / state["srt_name"]).read_text(encoding="utf-8-sig")
        self.assertEqual(txt.count("Привет"), 1)
        self.assertEqual(txt.count("Мир"), 1)
        self.assertEqual(srt.count("Привет"), 1)
        self.assertIn("00:00:01,000 --> 00:00:03,000", srt)

    def test_checkpoint_corruption_never_silently_resets(self):
        path = checkpoint_path(self.job, self.source, self.output)
        path.parent.mkdir(parents=True)
        path.write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Контрольная точка повреждена"):
            load_checkpoint(self.job, self.source, self.output)
        self.assertEqual(path.read_text(encoding="utf-8"), "not json")

    def test_restart_retains_old_results(self):
        with patch("backend.event"):
            state, path = begin_resume_job(self.job, self.source, self.output)
        state["segments"] = [[0, 1000, "Старый текст"]]
        state["cursor_ms"] = 60000
        save_checkpoint(path, state)
        render_transcripts(state, self.output, self.source)
        old_file = self.output / state["txt_name"]
        with patch("backend.event"):
            again, _ = begin_resume_job(dict(self.job, resume_policy="restart"),
                                        self.source, self.output)
        self.assertNotEqual(state["txt_name"], again["txt_name"])
        self.assertIn("Старый текст", old_file.read_text(encoding="utf-8-sig"))
        self.assertEqual(again["segments"], [])

    def test_resume_after_60_seconds_without_duplicates(self):
        with patch("backend.event"):
            state, path = begin_resume_job(self.job, self.source, self.output)

        calls = []
        class FakeModel:
            def transcribe(self, samples, **kwargs):
                calls.append(kwargs)
                return [SimpleNamespace(start=1, end=3, text="фраза")], SimpleNamespace(language="ru")

        def chunk(source, start, end):
            if start >= 58 and len(calls) == 1:
                raise RuntimeError("Имитация отключения питания")
            return np.zeros(round((end - start) * 16000), dtype=np.float32)

        with patch("backend.create_whisper_model", return_value=FakeModel()), \
             patch("backend.decode_transcription_chunk", side_effect=chunk), \
             patch("backend.media_duration", return_value=130.), \
             patch("backend.event"):
            with self.assertRaisesRegex(RuntimeError, "Имитация"):
                transcribe(self.source, self.source, self.output, self.job, 0, state, path)

        saved = load_checkpoint(self.job, self.source, self.output)
        self.assertEqual(saved["cursor_ms"], 60000)
        self.assertEqual(len(saved["segments"]), 1)
        txtname = saved["txt_name"]
        visited = []

        def recovered(source, start, end):
            visited.append(start)
            return np.zeros(round((end - start) * 16000), dtype=np.float32)

        with patch("backend.create_whisper_model", return_value=FakeModel()), \
             patch("backend.decode_transcription_chunk", side_effect=recovered), \
             patch("backend.media_duration", return_value=130.), \
             patch("backend.event"):
            transcribe(self.source, self.source, self.output, self.job, 0, saved, path)
        self.assertEqual(visited, [58., 118.])
        self.assertFalse(path.exists())
        text = (self.output / txtname).read_text(encoding="utf-8-sig")
        self.assertEqual(text.count("фраза"), 3)
        self.assertEqual(len(set(line for line in text.splitlines() if "фраза" in line)), 3)

    def test_video_audio_is_reused_on_retry(self):
        video = self.source.with_suffix(".mp4")
        video.write_bytes(b"video")
        job = dict(self.job, file=str(video), mode="both")
        mp3 = self.output / "lecture_audio.mp3"
        mp3.write_bytes(b"mp3 audio")
        with patch("backend.extract_audio", return_value=mp3) as extract, \
             patch("backend.transcribe", side_effect=RuntimeError("Failure")), \
             patch("backend.media_duration", return_value=120.), \
             patch("backend.event"):
            with self.assertRaisesRegex(RuntimeError, "Failure"):
                run_job(job)
        self.assertEqual(extract.call_count, 1)
        self.assertTrue(load_checkpoint(job, video, self.output)["audio_name"].endswith(".mp3"))

        with patch("backend.extract_audio") as extract, \
             patch("backend.transcribe", return_value=(None, None)) as transcription, \
             patch("backend.media_duration", return_value=120.), \
             patch("backend.event"):
            run_job(job)
        extract.assert_not_called()
        self.assertEqual(transcription.call_args.args[0], mp3)


if __name__ == "__main__":
    unittest.main()
