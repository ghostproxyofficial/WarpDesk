@echo off
setlocal

net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo Requesting administrator permission...
    if "%~1"=="" (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    ) else (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    )
    exit /b
)

echo Starting WarpDesk Agent stack...

set ROOT=%~dp0
set AGENT_DIR=%ROOT%agent
set VENV=%AGENT_DIR%\.venv
set PY_EXE=
set PY_VER=
set MONITOR_INDEX=%~1

if not exist "%AGENT_DIR%\app.py" (
    echo Missing backend entrypoint: %AGENT_DIR%\app.py
    pause
    exit /b 1
)

if not exist "%AGENT_DIR%\selkies_windows_launcher.py" (
    echo Missing launcher script: %AGENT_DIR%\selkies_windows_launcher.py
    pause
    exit /b 1
)

if "%MONITOR_INDEX%"=="" set MONITOR_INDEX=1

for /f "delims=" %%i in ('py -3.9 -c "import sys; print(sys.executable)" 2^>nul') do set PY_EXE=%%i
if not "%PY_EXE%"=="" set PY_VER=3.9

if "%PY_EXE%"=="" (
    for /f "delims=" %%i in ('py -3.13 -c "import sys; print(sys.executable)" 2^>nul') do set PY_EXE=%%i
    if not "%PY_EXE%"=="" set PY_VER=3.13
)

if "%PY_EXE%"=="" (
for /f "delims=" %%i in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set PY_EXE=%%i
if not "%PY_EXE%"=="" set PY_VER=3.12
)

if "%PY_EXE%"=="" (
    for /f "delims=" %%i in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do set PY_EXE=%%i
    if not "%PY_EXE%"=="" set PY_VER=3.11
)

if "%PY_EXE%"=="" (
    for /f "delims=" %%i in ('py -3.14 -c "import sys; print(sys.executable)" 2^>nul') do set PY_EXE=%%i
    if not "%PY_EXE%"=="" set PY_VER=3.14
)

if "%PY_EXE%"=="" (
    echo No supported Python runtime found for WarpDesk agent.
    echo Install Python 3.9, 3.13, 3.12, 3.11, or 3.14 and ensure the 'py' launcher is available.
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

if exist "%VENV%\Scripts\python.exe" (
    "%VENV%\Scripts\python.exe" -c "import sys; raise SystemExit(0 if '.'.join(map(str, sys.version_info[:2]))=='%PY_VER%' else 1)" >nul 2>nul
    if errorlevel 1 (
        echo Existing agent venv is not Python %PY_VER%. Recreating it...
        rmdir /s /q "%VENV%"
    )
)

if not exist "%VENV%\Scripts\python.exe" (
    echo [1/4] Creating Python %PY_VER% virtual environment...
    "%PY_EXE%" -m venv "%VENV%"
)

if errorlevel 1 (
    echo Failed to create Python virtual environment.
    pause
    exit /b 1
)

echo [2/4] Installing agent dependencies...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip wheel setuptools
"%VENV%\Scripts\python.exe" -m pip install -r "%AGENT_DIR%\requirements.txt"
if errorlevel 1 (
    echo Dependency installation failed.
    pause
    exit /b 1
)
"%VENV%\Scripts\python.exe" -m pip install dxcam >nul 2>nul

echo [2.2/4] Verifying critical imports...
"%VENV%\Scripts\python.exe" -c "import aiohttp, mss, numpy, pyautogui" >nul 2>nul
if errorlevel 1 (
    echo Failed to import one or more critical modules in the venv.
    if "%PY_VER%"=="3.14" (
        echo Python 3.14 may not yet have compatible wheels for one or more desktop capture dependencies.
        echo Install Python 3.13 or 3.12 and run start_all.bat again.
    )
    echo You can retry manually with:
    echo   %VENV%\Scripts\python.exe -m pip install -r agent\requirements.txt
    pause
    exit /b 1
)

echo [2.4/4] Releasing stale listeners on 8080/8443 (if any)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ports = @(8080,8443); $conns = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $ports -contains $_.LocalPort }; foreach($c in $conns){ $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue; if($null -ne $p -and @('python','python3','cmd','powershell') -contains $p.ProcessName.ToLower()){ Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }" >nul 2>nul

echo [2.45/4] Verifying ports are available...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$busy = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(8080,8443) }; if($busy){ $busy | Select-Object LocalAddress,LocalPort,OwningProcess | Format-Table -AutoSize; exit 1 }" >nul
if errorlevel 1 (
    echo Port 8080 or 8443 is still in use by another process.
    echo Close that app, then run start_all.bat again.
    echo Tip: Use PowerShell: Get-NetTCPConnection -State Listen ^| ? LocalPort -in 8080,8443
    pause
    exit /b 1
)

echo [2.5/4] Opening firewall ports for LAN testing (8080, 8443)...
netsh advfirewall firewall add rule name="WarpDesk Web 8080" dir=in action=allow protocol=TCP localport=8080 >nul 2>nul
netsh advfirewall firewall add rule name="WarpDesk Agent 8443" dir=in action=allow protocol=TCP localport=8443 >nul 2>nul

echo [3/4] Starting static web client on http://0.0.0.0:8080 ...
start cmd /k "cd /d %ROOT%web && title WarpDesk Web Client && python -m http.server 8080 --bind 0.0.0.0"

echo [4/4] Starting agent backend on https://0.0.0.0:8443 ...
start /high cmd /k "cd /d %AGENT_DIR% && title WarpDesk agent && set GST_ROOT=C:\Users\%USERNAME%\AppData\Local\Programs\gstreamer\1.0\msvc_x86_64 && set PATH=%GST_ROOT%\bin;%PATH% && set PYTHONPATH=%GST_ROOT%\lib\site-packages;%PYTHONPATH% && set GI_TYPELIB_PATH=%GST_ROOT%\lib\girepository-1.0 && set GST_SCHEDULING=sync && set GST_DEBUG=0 && set WEBRTC_ENCODER=mf && set WARPDESK_FPS=60 && set WARPDESK_MAX_FPS=120 && set WARPDESK_SCALE=100 && set WARPDESK_MAX_SESSIONS=2 && set WARPDESK_MONITOR_INDEX=%MONITOR_INDEX% && set WARPDESK_VIDEO_MAX_BITRATE_BPS=80000000 && set WARPDESK_SDP_MIN_BITRATE_KBPS=1500 && set WARPDESK_SDP_START_BITRATE_KBPS=8000 && set WARPDESK_CODEC=h264 && set WARPDESK_AUDIO_SOURCE=system && set WARPDESK_ALLOW_MIC_FALLBACK=0 && set WARPDESK_FORCE_RICH_TUI=0 && set WARPDESK_TUI=0 && set WARPDESK_LAUNCHER_PLAIN=1 && %VENV%\Scripts\python.exe selkies_windows_launcher.py"

echo.
echo All services started.
echo Open: http://localhost:8080
echo Connection URL: https://localhost:8443
echo Monitor Index: %MONITOR_INDEX%
echo Username: admin
echo Password: warpdesk

timeout /t 3 > nul
endlocal
exit /b 0
