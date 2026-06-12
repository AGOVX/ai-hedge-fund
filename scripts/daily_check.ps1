# daily_check.ps1 — 定期点検バッチ (Windows タスクスケジューラから起動)
#
# 実行内容:
#   1. watchlist_check     : Watch List 点検 (期日 / テクニカル / TDnet 開示)  — 毎回
#   2. portfolio snapshot  : equity-curve.csv に時価スナップショット追記        — 毎回
#   3. decision_review     : Pass/Watch 判断の事後スコアカード                  — 月曜のみ
#
# 出力: data/reports/*.md (レポート) + data/logs/daily_check-YYYYMMDD.log (実行ログ)
# 手動実行: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\daily_check.ps1
#
# CLAUDE.md 原則: 本スクリプトは読み取り・記録のみ。発注・口座アクセスは一切しない。

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot   # scripts/ の親 = リポジトリルート
Set-Location $repo

$env:PYTHONIOENCODING = "utf-8"

# --- ログ準備 -----------------------------------------------------------
$logDir = Join-Path $repo "data\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd"
$log = Join-Path $logDir "daily_check-$stamp.log"

function Write-Log([string]$msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    $line | Out-File -FilePath $log -Append -Encoding utf8
}

# 60日より古いログは削除
Get-ChildItem $logDir -Filter "daily_check-*.log" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-60) } |
    ForEach-Object { try { Remove-Item $_.FullName -Force -Confirm:$false } catch {} }

# --- python 解決 ---------------------------------------------------------
$python = $null
try { $python = (Get-Command python -ErrorAction Stop).Source } catch {}
if (-not $python) {
    Write-Log "FATAL: python が見つからない (PATH 未設定?)"
    exit 1
}
Write-Log "=== daily_check 開始 (python: $python) ==="

# --- 実行ヘルパー ---------------------------------------------------------
# cmd /c 経由で stdout/stderr をログへ直接追記する。PowerShell 5.1 で native
# コマンドに 2>&1 を使うと stderr 行が NativeCommandError に包まれてログが
# 汚れるため、PowerShell にストリームを触らせない。
$script:failures = 0
function Invoke-Step([string]$name, [string]$cliArgs) {
    Write-Log "--- $name ---"
    cmd /c "`"$python`" $cliArgs >> `"$log`" 2>&1"
    if ($LASTEXITCODE -ne 0) {
        Write-Log "NG: $name (exit $LASTEXITCODE)"
        $script:failures++
    } else {
        Write-Log "OK: $name"
    }
}

# --- 1. Watch List 点検 (毎回) -------------------------------------------
Invoke-Step "watchlist_check" "-m src.tools.watchlist_check"

# --- 2. ポートフォリオスナップショット (毎回) -----------------------------
Invoke-Step "portfolio snapshot" "-m src.tools.portfolio snapshot"

# --- 3. 判断スコアカード + パフォーマンス測定 (月曜のみ) -------------------
if ((Get-Date).DayOfWeek -eq "Monday") {
    Invoke-Step "decision_review" "-m src.tools.decision_review"
    Invoke-Step "performance" "-m src.tools.performance"
} else {
    Write-Log "decision_review / performance はスキップ (月曜のみ実行)"
}

Write-Log "=== daily_check 終了 (失敗 $script:failures 件) ==="
exit $(if ($script:failures) { 1 } else { 0 })
