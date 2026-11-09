@echo off
set DEST=%USERPROFILE%\llmhub
if not exist "%DEST%" mkdir "%DEST%"
copy llmhub.py "%DEST%\" >nul
copy models.json "%DEST%\" >nul
copy secret.py "%DEST%\" >nul
echo Installed to %DEST%
echo.
echo Store your API keys (once):
echo   python secret.py set nvidia/personal/api_key
echo   python secret.py set gemini/personal/api_key
echo   python secret.py set openai/personal/api_key
echo.
echo Then close opencode, run: python %DEST%\llmhub.py, start opencode.
