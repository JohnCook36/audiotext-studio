@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title AudioText Studio - сборка EXE установщика

echo ================================================
echo   AudioText Studio - сборка для Windows 11
 echo ================================================
echo.
where py >nul 2>&1
if errorlevel 1 (
    echo ОШИБКА: установите Python 3.12 с python.org
    goto :error
)
if not exist ".buildvenv\Scripts\python.exe" (
    echo [1/4] Создаю окружение сборки Python 3.12 ...
    py -3.12 -m venv .buildvenv
    if errorlevel 1 goto :error
)
set "PY=.buildvenv\Scripts\python.exe"
echo [2/4] Устанавливаю компоненты сборки ...
"%PY%" -m pip install --disable-pip-version-check -r requirements-build.txt
if errorlevel 1 goto :error

echo [3/4] Собираю приложение AudioTextStudio.exe ...
"%PY%" -m PyInstaller --noconfirm --clean audiotext.spec
if errorlevel 1 goto :error

set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo.
    echo Для установки нужен Inno Setup 6. Попробую установить через winget.
    where winget >nul 2>&1
    if errorlevel 1 (
        echo ОШИБКА: Inno Setup 6 не найден, winget недоступен.
        echo Установите Inno Setup с https://jrsoftware.org/isdl.php
        goto :error
    )
    winget install --id JRSoftware.InnoSetup -e -s winget -i --accept-source-agreements --accept-package-agreements
    if errorlevel 1 goto :error
    if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
    if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
)
if not defined ISCC (
   echo ОШИБКА: не удалось найти ISCC.exe после установки Inno Setup.
   goto :error
)

echo [4/4] Собираю установщик AudioTextStudio_Setup_1.1.0.exe ...
"%ISCC%" installer.iss
if errorlevel 1 goto :error

echo.
echo ГОТОВО! Установщик находится в папке installer.
start "" "%~dp0installer"
pause
exit /b 0

:error
echo.
echo Сборка не завершена. Сообщение об ошибке смотрите выше.
pause
exit /b 1