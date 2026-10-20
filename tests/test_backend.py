import tempfile
import unittest
import json
import os
from unittest.mock import patch
from pathlib import Path

from backend import parse_ffmpeg_timestamp, timecode, unique_path, validate_job, event, AUDIO, VIDEO


class BackendTests(unittest.TestCase):
    def test_timecode(self):
        self.assertEqual(timecode(3661.234), '01:01:01.234')
        self.assertEqual(timecode(2.5, True), '00:00:02,500')
        self.assertEqual(timecode(-1), '00:00:00.000')

    def test_ffmpeg_time(self):
        self.assertAlmostEqual(parse_ffmpeg_timestamp('00:00:03.250'), 3.25)
        self.assertIsNone(parse_ffmpeg_timestamp('N/A'))

    def test_unique_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'запись.txt'
            p.touch()
            self.assertEqual(unique_path(p).name, 'запись (2).txt')

    def test_validate_video(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'лекция.mp4'
            p.touch()
            src, out, is_video, mode = validate_job({'file': str(p), 'mode': 'both', 'model': 'small'})
            self.assertTrue(is_video)
            self.assertEqual(mode, 'both')
            self.assertEqual(out, p.parent / 'Результаты_распознавания')

    def test_audio_cannot_extract(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'лекция.mp3'
            p.touch()
            with self.assertRaisesRegex(ValueError, 'аудиофайлов'):
                validate_job({'file': str(p), 'mode': 'extract'})

    def test_event_to_file_without_console(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            with patch.dict(os.environ, {'ATS_EVENT_FILE': str(path)}):
                with patch('sys.stdout', None):
                    event('status', message='Привет, видео!')
                    event('progress', value=42)
            lines = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(lines, [
                {'type': 'status', 'message': 'Привет, видео!'},
                {'type': 'progress', 'value': 42},
            ])

    def test_extensions(self):
        self.assertIn('.mp4', VIDEO)
        self.assertIn('.flac', AUDIO)


if __name__ == '__main__':
    unittest.main()