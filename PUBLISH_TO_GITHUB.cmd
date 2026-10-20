@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title AudioText Studio - GitHub upload
set "REPO=https://github.com/JohnCook36/audiotext-studio.git"
echo.
echo  ============================================
echo   AudioText Studio - загрузка на GitHub
 echo  ============================================
echo.
echo  Перед запуском создайте ПУСТОЙ репозиторий:
echo  https://github.com/new?name=audiotext-studio
 echo.
echo  Имя: audiotext-studio
 echo  НЕ добавляйте README, .gitignore и License на сайте.
echo.
where git >nul 2>&1
if errorlevel 1 (
  echo  ОШИБКА: Git не найден. Установите Git for Windows.
  goto :error
)
if not exist ".git" (
  git init -b main
  if errorlevel 1 goto :error
)
git remote get-url origin >nul 2>&1
if errorlevel 1 (
  git remote add origin "%REPO%"
  if errorlevel 1 goto :error
)
for /f "delims=" %%G in ('git remote get-url origin') do set "ACTUAL_REMOTE=%%G"
if /i not "%ACTUAL_REMOTE%"=="%REPO%" (
  echo  У репозитория другой origin: %ACTUAL_REMOTE%
  echo  Для безопасности публикация остановлена.
  goto :error
)
git add .
if errorlevel 1 goto :error
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "Initial AudioText Studio desktop application and Windows installer CI"
  if errorlevel 1 (
    echo  Не удалось создать коммит. Настройте git user.name и user.email.
    goto :error
  )
)
git branch -M main
if errorlevel 1 goto :error
git push -u origin main
if errorlevel 1 goto :error
echo.
echo  Исходники отправлены!
echo  Посмотреть сборку: https://github.com/JohnCook36/audiotext-studio/actions
start "" "https://github.com/JohnCook36/audiotext-studio/actions"
pause
exit /b 0
:error
echo.
echo  Публикация не завершена. Исправьте сообщение об ошибке выше.
pause
exit /b 1