@echo off
setlocal
cd /d "%~dp0"
title Kritika launcher

rem ---- UTF-8 everywhere so game folders with accents or other non-English characters work ----
for /f "tokens=2 delims=:" %%a in ('chcp') do set OLDCP=%%a
chcp 65001 >nul
set PYTHONUTF8=1

rem ---- SERVER LOGGING (edit the value after the = sign) ----
rem   ask = ask once on the first launch and remember the answer in log_choice.txt (delete that file to be asked again)
rem   0   = no logging
rem   1   = save the log to a file (one file per connection in server\logs)
rem   2   = show the log live in the server window (nothing is saved)
rem Any value other than "ask" overrides the saved answer.
set LOG_MODE=ask

rem ---- Python 3.10+ (the only thing this needs; the server uses just Python's standard library) ----
set PY=
where py >nul 2>nul && py -3 -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>nul && set PY=py -3
if not defined PY (
  where python >nul 2>nul && python -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>nul && set PY=python
)
if not defined PY (
  echo Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) and run this again.
  pause & exit /b 1
)

rem ---- game folder: saved in game_dir.txt after the first run ----
set KRITIKA_DIR=
if exist game_dir.txt set /p KRITIKA_DIR=<game_dir.txt
:askdir
if defined KRITIKA_DIR if exist "%KRITIKA_DIR%\KRITIKA_Client.exe" goto havedir
set KRITIKA_DIR=
for /f "usebackq delims=" %%i in (`%PY% -X utf8 server\find_game.py`) do set KRITIKA_DIR=%%i
if defined KRITIKA_DIR if exist "%KRITIKA_DIR%\KRITIKA_Client.exe" goto havedir
:promptdir
echo.
echo Enter the full path of the Kritika game folder (the folder that contains KRITIKA_Client.exe).
set KRITIKA_DIR=
set /p KRITIKA_DIR=Path:
set KRITIKA_DIR=%KRITIKA_DIR:"=%
if defined KRITIKA_DIR if exist "%KRITIKA_DIR%\KRITIKA_Client.exe" goto havedir
goto promptdir
:havedir
>game_dir.txt echo %KRITIKA_DIR%
echo Game folder: %KRITIKA_DIR%

rem ---- point the client at this server (original files are kept once as *.original) ----
if not exist "%KRITIKA_DIR%\WorldList.xml.original" if exist "%KRITIKA_DIR%\WorldList.xml" copy /y "%KRITIKA_DIR%\WorldList.xml" "%KRITIKA_DIR%\WorldList.xml.original" >nul
copy /y "client_config\WorldList.xml" "%KRITIKA_DIR%\WorldList.xml" >nul
copy /y "client_config\SystemConfig_offline.xml" "%KRITIKA_DIR%\SystemConfig_offline.xml" >nul

rem ---- logging choice (see LOG_MODE at the top) ----
if /i not "%LOG_MODE%"=="ask" goto logset
set LOG_MODE=
if exist log_choice.txt set /p LOG_MODE=<log_choice.txt
if defined LOG_MODE goto logset
echo.
echo Server logging:
echo   0 = no logging
echo   1 = save the log to a file (server\logs)
echo   2 = show the log live in the server window
set LOG_ANSWER=0
set /p LOG_ANSWER=Choose 0, 1 or 2 [Enter = 0]:
set LOG_MODE=0
if "%LOG_ANSWER:~0,1%"=="1" set LOG_MODE=1
if "%LOG_ANSWER:~0,1%"=="2" set LOG_MODE=2
>log_choice.txt echo %LOG_MODE%
echo Saved. Change it later by editing LOG_MODE at the top of start_kritika.bat, or delete log_choice.txt to be asked again.
:logset
set KRITIKA_LOG=%LOG_MODE%

rem ---- stop here if a server is already running (the client would silently talk to the old one) ----
powershell -NoProfile -Command "try{ $c=New-Object Net.Sockets.TcpClient('127.0.0.1',10331); $c.Close(); exit 0 }catch{ exit 1 }"
if not errorlevel 1 (
  echo A Kritika server is already running ^(port 10331 is in use^). Close its window and run this again.
  pause & exit /b 1
)

rem ---- server in its own window (stays open so you can read its output) ----
start "Kritika server" cmd /k "chcp 65001 >nul & %PY% server\stub_server.py"
echo Waiting for the server to listen ...
powershell -NoProfile -Command "$t=Get-Date; while(((Get-Date)-$t).TotalSeconds -lt 120){ try{ $c=New-Object Net.Sockets.TcpClient('127.0.0.1',10331); $c.Close(); exit 0 }catch{ Start-Sleep -Milliseconds 500 } }; exit 1"
if errorlevel 1 (
  echo The server did not start listening on port 10331 within 120 seconds. Read the "Kritika server" window.
  pause & exit /b 1
)

rem ---- client ----
cd /d "%KRITIKA_DIR%"
start "" "KRITIKA_Client.exe" --channelType ST --config SystemConfig_offline.xml
echo Client started.
if defined OLDCP chcp %OLDCP% >nul
endlocal
