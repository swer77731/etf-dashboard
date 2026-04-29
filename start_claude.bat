@echo off
REM ETF 觀察室 — Claude Code 快捷啟動
REM 用法:雙擊桌面「ETF Claude Code」捷徑

cd /d "C:\projects\etf_dashboard"

REM 若有 venv,啟用(Claude Code 本身不需 Python,但 user 可能想跑專案 script)
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

REM 啟動 Claude Code(假設 claude 已加入 PATH 由 npm 或 winget 安裝)
where claude >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Claude Code CLI 找不到
    echo 請先安裝:npm install -g @anthropic-ai/claude-code
    pause
    exit /b 1
)

claude
