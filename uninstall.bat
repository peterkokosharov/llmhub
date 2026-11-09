@echo off
set DEST=%USERPROFILE%\llmhub
if exist "%DEST%" (
    rmdir /s /q "%DEST%"
    echo Removed %DEST%
) else (
    echo Not installed at %DEST%
)
