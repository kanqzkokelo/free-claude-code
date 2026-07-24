@echo off
REM Check if Node.js is installed
node --version > nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo WARNING: Node.js is not installed.
    echo Claude Code CLI requires Node.js (https://nodejs.org).
    echo Install Node.js manually, then run: npm install -g @anthropic-ai/claude-code
    echo.
    pause
    exit /b 1
)
exit /b 0
