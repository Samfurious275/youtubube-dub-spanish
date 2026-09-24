@echo off
REM Windows counterpart of run.sh.
REM First run installs everything; after that it just runs dub.py with your arguments.
setlocal
cd /d "%~dp0"

where ffmpeg >nul 2>&1 || (
  echo Installing ffmpeg...
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements || (
    echo.
    echo Could not install ffmpeg automatically.
    echo Install it from https://ffmpeg.org/download.html and make sure ffmpeg.exe is on your PATH.
    exit /b 1
  )
)

where yt-dlp >nul 2>&1 || (
  echo Installing yt-dlp...
  winget install --id yt-dlp.yt-dlp -e --accept-source-agreements --accept-package-agreements || (
    echo.
    echo Could not install yt-dlp automatically.
    echo Install it from https://github.com/yt-dlp/yt-dlp/releases and put yt-dlp.exe on your PATH.
    exit /b 1
  )
)

if not exist ".venv\Scripts\python.exe" (
  REM torch 2.2.2 has no Windows wheel for 3.12+, so pin the interpreter to 3.11
  py -3.11 --version >nul 2>&1 || (
    echo.
    echo Python 3.11 is required but was not found.
    echo Install it from https://www.python.org/downloads/release/python-3119/
    echo ^(tick "Add python.exe to PATH" in the installer^) and run this again.
    exit /b 1
  )
  py -3.11 -m venv .venv || exit /b 1
  .venv\Scripts\python.exe -m pip install --upgrade pip || exit /b 1
  .venv\Scripts\python.exe -m pip install -r requirements.txt || exit /b 1
)

.venv\Scripts\python.exe dub.py %*
