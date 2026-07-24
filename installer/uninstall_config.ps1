<#
.SYNOPSIS
    Pre-uninstall cleanup for Free Claude Code.
    Removes scheduled task and environment variables.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$TaskName = "FreeClaudeCodeProxy"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$RunValueName = "FreeClaudeCode"

Write-Host "=== Free Claude Code Uninstall ==="
Write-Host ""

# ── Stop running proxy processes ────────────────────────────────────────────
Write-Host "Stopping running proxy processes..."
try {
    $procs = Get-Process -Name "python" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        $cmdLine = (Get-WmiObject Win32_Process -Filter "ProcessId=$($p.Id)").CommandLine
        if ($cmdLine -like "*server.py*" -or $cmdLine -like "*FreeClaudeCode*") {
            $p.Kill()
            Write-Host "  Killed PID $($p.Id)"
        }
    }
} catch {
    Write-Host "  No running processes found"
}

# ── Remove scheduled task ────────────────────────────────────────────────────
Write-Host "Removing scheduled task..."
try {
    schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
    Write-Host "  [OK] Scheduled task removed"
} catch {
    Write-Host "  Could not remove task: $_"
}

# ── Remove registry run key ─────────────────────────────────────────────────
Write-Host "Removing registry run key..."
try {
    if (Test-Path $RunKey) {
        Remove-ItemProperty -Path $RunKey -Name $RunValueName -ErrorAction SilentlyContinue
        Write-Host "  [OK] Registry run key removed"
    }
} catch {
    Write-Host "  Could not remove registry key: $_"
}

# ── Remove environment variables ─────────────────────────────────────────────
Write-Host "`nEnvironment variables..."
Write-Host "  These system env vars were set during install and are safe to keep:"
Write-Host "  - ANTHROPIC_BASE_URL"
Write-Host "  - ANTHROPIC_AUTH_TOKEN"
Write-Host "  - CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"
Write-Host "  - CLAUDE_CODE_AUTO_COMPACT_WINDOW"
Write-Host ""
Write-Host "  To remove them manually as admin:"
Write-Host "    [Environment]::SetEnvironmentVariable('ANTHROPIC_BASE_URL',`$null,'Machine')"
Write-Host "    [Environment]::SetEnvironmentVariable('ANTHROPIC_AUTH_TOKEN',`$null,'Machine')"
Write-Host "    [Environment]::SetEnvironmentVariable('CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY',`$null,'Machine')"
Write-Host "    [Environment]::SetEnvironmentVariable('CLAUDE_CODE_AUTO_COMPACT_WINDOW',`$null,'Machine')"

Write-Host ""
Write-Host "=== Uninstall Complete ==="
