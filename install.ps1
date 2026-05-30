#!/usr/bin/env pwsh
# group-daily Windows 安装脚本
# 用法: powershell -ExecutionPolicy Bypass -File install.ps1

Write-Host "group-daily Windows 适配版 - 安装程序" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. 检查 Python
Write-Host "[1/4] 检查 Python..." -ForegroundColor Yellow
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $python) {
    Write-Host "  ❌ 未找到 Python。请从 https://www.python.org/downloads/ 安装 Python 3.10+" -ForegroundColor Red
    exit 1
}
Write-Host "  ✅ Python $(& $python.Source --version 2>&1)" -ForegroundColor Green

# 2. 安装 Python 依赖
Write-Host "[2/4] 安装 Python 依赖..." -ForegroundColor Yellow
$deps = @("Pillow", "pywxdump")
foreach ($dep in $deps) {
    Write-Host "  安装 $dep ..."
    & $python.Source -m pip install $dep --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ⚠ $dep 安装失败，尝试继续..." -ForegroundColor Yellow
    }
}

# 可选依赖
Write-Host "  安装可选依赖 openai-whisper (语音转写) ..."
& $python.Source -m pip install openai-whisper --quiet 2>&1 | Out-Null
Write-Host "  ✅ 依赖安装完成" -ForegroundColor Green

# 3. 设置环境变量（可选）
Write-Host "[3/4] 配置环境..." -ForegroundColor Yellow
$skillDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "  Skill 目录: $skillDir"

# 检查是否已设置 GROUP_DAILY_VAULT
$vault = [Environment]::GetEnvironmentVariable("GROUP_DAILY_VAULT", "User")
if (-not $vault) {
    $defaultVault = "$env:USERPROFILE\Documents\GroupDaily"
    Write-Host "  GROUP_DAILY_VAULT 未设置，默认使用: $defaultVault"
    Write-Host "  如需自定义，运行: setx GROUP_DAILY_VAULT ""你的路径"""
    New-Item -ItemType Directory -Force -Path $defaultVault | Out-Null
}

# 4. 运行环境自检
Write-Host "[4/4] 运行环境自检..." -ForegroundColor Yellow
Write-Host ""
& $python.Source "$skillDir\scripts\check_env.py"
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "✅ 安装完成！环境就绪，可以运行 group-daily。" -ForegroundColor Green
} else {
    Write-Host "⚠ 安装基本完成，但环境检查有警告。请查看上方提示。" -ForegroundColor Yellow
    Write-Host "  核心依赖: pywxdump + Pillow"
    Write-Host "  可选依赖: openai-whisper (语音转写)"
}

Write-Host ""
Write-Host "使用方法（在 Claude Code 中）:" -ForegroundColor Cyan
Write-Host "  - 检查环境: python scripts/check_env.py"
Write-Host "  - 查看微信信息: python scripts/wechat_windows.py info"
Write-Host "  - 导出聊天记录: python scripts/wechat_windows.py history ""群名"" --limit 1000"
Write-Host "  - 查看群成员: python scripts/wechat_windows.py members ""群名"" --json"
