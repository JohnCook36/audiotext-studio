# AudioText Studio

Локальное Windows-приложение для извлечения аудиодорожек из видео и распознавания речи с помощью faster-whisper.

## Возможности
- Поддержка MP4, MKV, MOV, AVI, WebM и популярных аудиоформатов.
- Экспорт аудио в MP3, M4A, WAV.
- Распознавание речи на устройстве, временные метки, TXT и SRT.
- Выбор модели Whisper (tiny, base, small, medium, large-v3).
- Графический интерфейс на Python/Tkinter, ход обработки, отмена.
- Windows установщик с помощью PyInstaller + Inno Setup.

## Скачать установщик
Перейдите на вкладку [Actions](../../actions/workflows/build-windows.yml), откройте последний успешный запуск **Build Windows installer**, скачайте artifact **AudioTextStudio-Windows-Installer** и распакуйте архив. В нём будет `AudioTextStudio_Setup_1.0.0.exe`.

Подробности: [README_RU.md](README_RU.md), [GET_EXE.md](GET_EXE.md).

> Модели Whisper скачиваются при первом использовании. Личные видео и аудиозаписи в GitHub не загружаются.
