@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

set "PYTHONW="
if defined VG_GATEWAY_PYTHON (
    for %%I in ("%VG_GATEWAY_PYTHON%") do set "PYTHONW=%%~dpIpythonw.exe"
    if not exist "%PYTHONW%" set "PYTHONW="
)

if not defined PYTHONW if exist "%USERPROFILE%\miniconda3\envs\vg-gateway\pythonw.exe" set "PYTHONW=%USERPROFILE%\miniconda3\envs\vg-gateway\pythonw.exe"
if not defined PYTHONW if exist "%USERPROFILE%\anaconda3\envs\vg-gateway\pythonw.exe" set "PYTHONW=%USERPROFILE%\anaconda3\envs\vg-gateway\pythonw.exe"
if not defined PYTHONW if exist "D:\Users\%USERNAME%\miniconda3\envs\vg-gateway\pythonw.exe" set "PYTHONW=D:\Users\%USERNAME%\miniconda3\envs\vg-gateway\pythonw.exe"
if not defined PYTHONW if exist "C:\ProgramData\miniconda3\envs\vg-gateway\pythonw.exe" set "PYTHONW=C:\ProgramData\miniconda3\envs\vg-gateway\pythonw.exe"

if not defined PYTHONW if defined CONDA_EXE (
    for /f "delims=" %%I in ('"%CONDA_EXE%" info --base 2^>nul') do if exist "%%I\envs\vg-gateway\pythonw.exe" set "PYTHONW=%%I\envs\vg-gateway\pythonw.exe"
)
if not defined PYTHONW (
    for /f "delims=" %%I in ('conda info --base 2^>nul') do if exist "%%I\envs\vg-gateway\pythonw.exe" set "PYTHONW=%%I\envs\vg-gateway\pythonw.exe"
)

if not defined PYTHONW (
    echo VoiceGeneration: vg-gateway environment was not found.
    echo Create it first or set VG_GATEWAY_PYTHON to its python.exe.
    pause
    exit /b 1
)

start "" "%PYTHONW%" "%CD%\scripts\tray.py"
exit /b 0
