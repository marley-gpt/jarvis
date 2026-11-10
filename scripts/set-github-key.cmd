@echo off
rem Double-click from File Explorer: sets the GitHub key for the Claude GitHub plugin.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set-github-key.ps1"
