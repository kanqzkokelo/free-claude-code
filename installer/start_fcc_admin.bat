@echo off
title Free Claude Code Admin UI
echo Starting Free Claude Code Admin UI...
echo.
echo If the proxy isn't running, start it with:
echo   net start FCCProxy
echo.
start http://localhost:8082/admin
echo Admin UI should open in your browser.
echo If nothing happens, go to: http://localhost:8082/admin
pause
