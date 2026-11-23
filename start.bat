@echo off
chcp 65001 >nul
title wangsiEDA
cd /d "%~dp0"
where python >nul 2>nul || (echo [x] Python not found in PATH. & pause & exit /b 1)
python -X utf8 wangsi.py up
