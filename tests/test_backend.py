import tempfile
import unittest
import json
import os
import subprocess
import sys
from unittest.mock import patch
from pathlib import Path

from backend import parse_ffmpeg_timestamp, timecode, unique_path, validate_job, event, extract_audio, AUDIO, VIDEO


class BackendTests(unittest.TestCase):
    def test_timecode(self):
        self.assertEqual(timecode(3661.234), '01:01:01.234')
        self.assertEqual(timecode(2.5, True), '00:00:02,500')
        self.assertEqual(timecode(-1), '00:00:00.000')

    def test_ffmpeg_time(self):
        self.assertAlmostEqual(parse_ffmpeg_timestamp('00:00:03.250'), 3.25)
        self.assertIsNone(parse_ffmpeg_timestamp('N/A'))


    def test_ffmpeg_launch_does_not_open_terminal(self):
        # This test runs on a Windows runner during installer CI.
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'lecture.mp4'
            with patch('backend.event'):
                with patch('backend.find_ffmpeg', return_value='ffmpeg.exe'):
                    with patch('backend.subprocess.Popen', side_effect=OSError('test launch')) as launcher:
                        with self.assertRaisesRegex(RuntimeError, 'Не удалось запустить FFmpeg'):
                            extract_audio(source, Path(directory), 'mp3', None)
            expected = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            self.assertEqual(launcher.call_args.kwargs['creationflags'], expected)
            self.assertEqual(launcher.call_args.kwargs['stdout'], subprocess.PIPE)

    def test_device_selection(self):
        from backend import whisper_device_options
        self.assertEqual(whisper_device_options('cpu'), {'device': 'cpu', 'compute_type': 'int8'})
        self.assertEqual(whisper_device_options('cuda'), {'device': 'cuda', 'compute_type': 'float16'})
        with self.assertRaises(ValueError):
            whisper_device_options('auto')

    def test_reject_unknown_device(self):
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / 'record.wav'
            src.touch()
            with self.assertRaisesRegex(ValueError, 'устройство'):
                validate_job({'file': str(src), 'mode': 'transcribe', 'device': 'invalid'})

    def test_cpu_model_is_explicitly_cpu(self):
        from backend import create_whisper_model
        with patch('faster_whisper.WhisperModel') as make:
            created = create_whisper_model('small', 'cpu')
        make.assert_called_once_with('small', device='cpu', compute_type='int8')
        self.assertIs(created, make.return_value)

    def test_cuda_model_is_explicitly_cuda(self):
        from backend import create_whisper_model
        with patch('ctranslate2.get_cuda_device_count', return_value=1):
            with patch('faster_whisper.WhisperModel') as make:
                created = create_whisper_model('small', 'cuda')
        make.assert_called_once_with('small', device='cuda', compute_type='float16')
        self.assertIs(created, make.return_value)

    def test_cuda_does_not_fallback_to_cpu(self):
        from backend import create_whisper_model
        with patch('ctranslate2.get_cuda_device_count', return_value=0):
            with patch('faster_whisper.WhisperModel') as make:
                with self.assertRaisesRegex(RuntimeError, 'NVIDIA GPU'):
                    create_whisper_model('small', 'cuda')
        make.assert_not_called()

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
            self.assertEqual(out, (p.parent / 'Результаты_распознавания').resolve())

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
