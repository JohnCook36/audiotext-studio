"""Offline unit tests: no gated Hugging Face model and no PyTorch downloads."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from speaker_diarization import (
    align_speakers, parse_transcript, render_speakers, rename_speakers,
    run_diarization, save_speaker_document, validate_speaker_settings,
)


class SpeakerDiarizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / "Лекция.wav"
        self.source.write_bytes(b"fake-media")
        self.txt = self.folder / "Лекция_transcript.txt"
        self.txt.write_text(
            "Источник: Лекция.wav\nМодель: small; язык: ru\n\n"
            "[00:00:01.000 — 00:00:03.000] Привет всем\n"
            "[00:00:05.000 — 00:00:06.000] Вопрос?\n"
            "[00:00:07.000 — 00:00:08.000] Ответ\n"
            "[00:00:12.000 — 00:00:13.000] Неизвестный голос\n",
            encoding="utf-8-sig",
        )

    def test_parse_only_our_timestamped_lines(self):
        self.assertEqual(len(parse_transcript(self.txt)), 4)
        self.assertEqual(parse_transcript(self.txt)[0], [1000, 3000, "Привет всем"])
        empty = self.folder / "other.txt"
        empty.write_text("Just plain text", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "временными метками"):
            parse_transcript(empty)

    def test_stable_speakers_across_many_minutes(self):
        segments = [[1000, 3000, "преподаватель"],
                    [120000, 121000, "студент"],
                    [7000000, 7001000, "преподаватель снова"]]
        turns = [[0, 4000, "A"], [119000, 122000, "B"], [6999000, 7002000, "A"]]
        mapped, names = align_speakers(segments, turns)
        self.assertEqual([m[3] for m in mapped], ["A", "B", "A"])
        self.assertEqual(names, {"A":"Говорящий 1","B":"Говорящий 2"})

    def test_assign_dominant_overlap_with_unrecognized_gap(self):
        segments = [[2000, 5000, "first"], [8000, 9000, "gap"]]
        turns = [[1000, 4200, "A"], [4200, 6000, "B"]]
        mapped, names = align_speakers(segments, turns)
        self.assertEqual(mapped[0][3], "A")
        self.assertIsNone(mapped[1][3])
        self.assertEqual(len(names), 2)

    def test_render_and_rename_without_repeat_transcription(self):
        mapped, names = align_speakers(parse_transcript(self.txt),
            [[0, 3500, "A"], [4500, 9000, "B"]])
        doc = {
            "schema": 1, "source_name": self.source.name,
            "txt_name": "sample_speakers.txt",
            "srt_name": "sample_speakers.srt",
            "speaker_names": names, "segments": mapped,
        }
        metadata = self.folder / "sample_speakers.json"
        save_speaker_document(metadata, doc)
        render_speakers(doc, self.folder)
        updated = rename_speakers(metadata, {"A":"Преподаватель","B":"Студент"})
        self.assertEqual(updated["speaker_names"]["A"], "Преподаватель")
        txt = (self.folder / "sample_speakers.txt").read_text(encoding="utf-8-sig")
        srt = (self.folder / "sample_speakers.srt").read_text(encoding="utf-8-sig")
        self.assertIn("Преподаватель: Привет всем", txt)
        self.assertIn("Студент: Вопрос?", srt)
        self.assertIn("Говорящий не определён", txt)
        self.assertEqual(srt.count("-->"), 4)
        with self.assertRaises(ValueError):
            rename_speakers(metadata, {"A":" "})

    def test_validate_speaker_count_and_device(self):
        validate_speaker_settings({"speaker_count":0,"speaker_device":"cpu"})
        validate_speaker_settings({"speaker_count":12,"speaker_device":"cuda"})
        with self.assertRaisesRegex(ValueError, "Количество"):
            validate_speaker_settings({"speaker_count":1})
        with self.assertRaisesRegex(ValueError, "CPU"):
            validate_speaker_settings({"speaker_device":"unknown"})

    def test_existing_txt_does_not_run_whisper(self):
        from backend import run_job
        job = {"file":str(self.source),"mode":"speakers_only","transcript_txt":str(self.txt),
               "speaker_count":2,"speaker_device":"cpu","srt":True}
        with patch("speaker_diarization.ensure_runtime", return_value=Path("python")), \
             patch("speaker_diarization.external_runtime_script", return_value=Path("helper.py")), \
             patch("speaker_diarization.decode_mono_wav") as decode, \
             patch("speaker_diarization.run_console") as run, \
             patch("backend.transcribe") as whisper, \
             patch("backend.event"):
            def fake_runtime(command, **kwargs):
                Path(command[3]).write_text(json.dumps([
                    [0, 3500, "A"],[4500, 11000, "B"]
                ]), encoding="utf-8")
            run.side_effect=fake_runtime
            run_job(job)
            whisper.assert_not_called()
            self.assertEqual(decode.call_count, 1)
        exports = list(self.folder.glob("*_speakers.txt"))
        self.assertEqual(len(exports), 1)
        self.assertIn("Говорящий 2", exports[0].read_text(encoding="utf-8-sig"))

    def test_speaker_failure_does_not_delete_whisper_text(self):
        original=self.txt.read_bytes()
        with patch("speaker_diarization.ensure_runtime", side_effect=RuntimeError("Offline")), \
             patch("backend.event"):
            with self.assertRaisesRegex(RuntimeError, "Offline"):
                run_diarization(self.source,self.txt,self.folder,{})
        self.assertEqual(self.txt.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
