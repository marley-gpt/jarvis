@echo off
rem JARVIS CLI for cmd/PowerShell. Requires Python 3.10+.
where py >NUL 2>NUL
if %errorlevel%==0 (set "JARVIS_PY=py -3") else (set "JARVIS_PY=python")
%JARVIS_PY% "%~dp0..\core\jarvis_cli.py" %*
