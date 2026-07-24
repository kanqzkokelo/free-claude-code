<#
.SYNOPSIS
    Post-install configuration for Free Claude Code installer.
    Handles Python install, venv setup, service/startup, env vars.
.PARAMETER InstallDir
    Installation directory (e.g., "C:\Program Files\FCC")
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$BinDir = Join-Path $InstallDir "bin"
$SrcDir = Join-Path $InstallDir "src"
$VenvDir = Join-Path $InstallDir "venv"
$LogDir = Join-Path $InstallDir "logs"
$UvExe = Join-Path $BinDir "uv.exe"
$DotEnv = Join-Path $InstallDir ".env"
$TaskName = "FreeClaudeCodeProxy"
$PythonVersion = "3.14.0"

function Write-Step($Message) {
    Write-Host ""
    Write-Host "==> $Message"
}

function Write-Result($Icon, $Message) {
    Write-Host "  $Icon $Message"
}

# ── Start ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================="
Write-Host "  Free Claude Code - Post-Install Setup"
Write-Host "========================================="
Write-Host "  Install: $InstallDir"
Write-Host "  Source:  $SrcDir"
Write-Host "  Venv:    $VenvDir"

# ── Step 1: Install Python 3.14 ──────────────────────────────────────────────
Write-Step "1/6: Installing Python $PythonVersion..."
if (Test-Path $UvExe) {
    & $UvExe python install $PythonVersion 2>&1
    Write-Result "[OK]" "Python $PythonVersion ready (exit $LASTEXITCODE)"
} else {
    Write-Result "[WARN]" "uv.exe not found at $UvExe"
}

# ── Step 2: Create Virtual Environment ──────────────────────────────────────
Write-Step "2/6: Creating Python virtual environment..."
if (Test-Path $UvExe) {
    & $UvExe venv $VenvDir --python $PythonVersion 2>&1
    Write-Result "[OK]" "Virtual environment created"
} else {
    python -m venv $VenvDir 2>&1
    Write-Result "[OK]" "Virtual environment created (system Python)"
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Result "[FAIL]" "Python venv not found at $VenvPython"
    return
}

$pyVer = & $VenvPython --version 2>&1
Write-Result "[OK]" "Python: $pyVer"

# ── Step 3: Install Dependencies ────────────────────────────────────────────
Write-Step "3/6: Installing Free Claude Code and dependencies..."
Write-Host "  (Downloading from PyPI - may take a few minutes)"
if (Test-Path $UvExe) {
    & $UvExe pip install --python $VenvDir -e $SrcDir 2>&1
} else {
    & $VenvPython -m pip install -e $SrcDir 2>&1
}

# Verify
try {
    $test = & $VenvPython -c "from api.app import create_app; print('import OK')" 2>&1
    Write-Result "[OK]" "Package verified: $test"
} catch {
    Write-Result "[WARN]" "Import test: $_"
}

# Copy .env file into the src dir so the app finds its config
if (Test-Path $DotEnv) {
    Copy-Item $DotEnv $SrcDir -Force
    Write-Result "[OK]" ".env copied to src directory"
}

# ── Step 4: Install Claude Code CLI ─────────────────────────────────────────
Write-Step "4/6: Installing Claude Code CLI..."
try {
    $nodeVer = & node --version 2>&1
    if ($?) {
        Write-Result "[OK]" "Node.js $nodeVer"
        & npm install -g @anthropic-ai/claude-code 2>&1
        if ($?) {
            $ccVer = & claude --version 2>&1
            Write-Result "[OK]" "Claude Code $(if($?){$ccVer}else{'installed'})"
        }
    } else { throw }
} catch {
    Write-Result "[WARN]" "Could not install Claude Code CLI"
    Write-Result "[HINT]" "Install Node.js from https://nodejs.org then: npm install -g @anthropic-ai/claude-code"
}

# ── Step 5: Set System Environment Variables ─────────────────────────────────
Write-Step "5/6: Setting system environment variables..."
$EnvVars = @{
    "ANTHROPIC_BASE_URL" = "http://localhost:8082"
    "ANTHROPIC_AUTH_TOKEN" = "freecc"
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY" = "1"
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW" = "190000"
}

foreach ($entry in $EnvVars.GetEnumerator()) {
    try {
        $current = [Environment]::GetEnvironmentVariable($entry.Key, "Machine")
        if ($current -ne $entry.Value) {
            [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Machine")
            Write-Result "[SET]" "$($entry.Key) = $($entry.Value)"
        } else {
            Write-Result "[OK]"  "$($entry.Key) already set"
        }
    } catch {
        Write-Result "[FAIL]" "$($entry.Key): $_"
    }
}

# Add npm to system PATH
try {
    $npmPrefix = & npm config get prefix 2>&1
    if ($? -and $npmPrefix) {
        $currentPath = [Environment]::GetEnvironmentVariable("Path", "Machine")
        if ($currentPath -and $currentPath -notlike "*$npmPrefix*") {
            [Environment]::SetEnvironmentVariable("Path", "$currentPath;$npmPrefix", "Machine")
            Write-Result "[SET]" "npm added to system PATH"
        }
    }
} catch { }

# ── Step 6: Auto-start via Task Scheduler ───────────────────────────────────
Write-Step "6/6: Setting up auto-start (Task Scheduler)..."


# Create a small launcher batch file that the scheduled task will run
$launcherContent = @"
@echo off
REM Free Claude Code - Auto-start Launcher
REM This file is called by Windows Task Scheduler
cd /d "$SrcDir"
start /b "" "$VenvPython" "$SrcDir\server.py"
"@

$launcherPath = Join-Path $InstallDir "start_proxy.bat"
Set-Content -Path $launcherPath -Value $launcherContent -Encoding ASCII
Write-Result "[OK]" "Created launcher: $launcherPath"

# Remove existing task if present
try {
    schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
    Write-Result "[OK]" "Removed old scheduled task"
} catch { }

# Create the scheduled task
# Run at user logon, with highest privileges, restart if it fails
try {
    $taskCmd = "schtasks /Create /TN `"$TaskName`" /TR `"$launcherPath`" /SC ONLOGON /DELAY 0000:15 /RL HIGHEST /F"
    Write-Host "  Running: $taskCmd"
    Invoke-Expression $taskCmd 2>&1 | ForEach-Object { Write-Host "    $_" }

    if ($?) {
        Write-Result "[OK]" "Scheduled task created (runs at logon)"
    } else {
        # Fall back to HKCU Run registry
        throw "schtasks failed"
    }
} catch {
    Write-Result "[WARN]" "Task Scheduler failed, using Registry Run key..."
    # Fallback: use registry run key
    try {
        $runPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
        Set-ItemProperty -Path $runPath -Name "FreeClaudeCode" -Value $launcherPath
        Write-Result "[OK]" "Registry Run key set"
    } catch {
        Write-Result "[FAIL]" "Could not set auto-start: $_"
        Write-Result "[HINT]" "Add '$launcherPath' to your startup folder"
    }
}

# ── Start the proxy NOW ────────────────────────────────────────────────────
Write-Step "Starting Free Claude Code proxy..."
try {
    $logFile = Join-Path $LogDir "fcc-service.log"
    $errFile = Join-Path $LogDir "fcc-service-error.log"

    # Start the process hidden
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $VenvPython
    $psi.Arguments = "`"$SrcDir\server.py`""
    $psi.WorkingDirectory = $SrcDir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.EnvironmentVariables["ANTHROPIC_BASE_URL"] = "http://localhost:8082"
    $psi.EnvironmentVariables["ANTHROPIC_AUTH_TOKEN"] = "freecc"
    $psi.EnvironmentVariables["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    $psi.EnvironmentVariables["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "190000"
    $psi.EnvironmentVariables["FCC_OPEN_BROWSER"] = "false"

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $proc.Start() | Out-Null

    Start-Sleep -Seconds 3

    if (-not $proc.HasExited) {
        Write-Result "[OK]" "Proxy started (PID: $($proc.Id))"
    } else {
        Write-Result "[WARN]" "Proxy exited immediately - checking..."
        $stderr = $proc.StandardError.ReadToEnd()
        if ($stderr) { Write-Host "    stderr: $stderr" }
    }
} catch {
    Write-Result "[WARN]" "Could not start proxy: $_"
    Write-Result "[HINT]" "Start it manually: $VenvPython $SrcDir\server.py"
}

# ── Summary ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================="
Write-Host "  INSTALLATION COMPLETE"
Write-Host "========================================="
Write-Host ""
Write-Host "  Free Claude Code is running now."
Write-Host "  It will auto-start when you log in."
Write-Host ""
Write-Host "  Admin UI:   http://localhost:8082/admin"
Write-Host "  Logs:       $LogDir"
Write-Host ""
Write-Host "  To use Claude Code in any terminal:"
Write-Host "    claude"
Write-Host ""
Write-Host "  Pre-configured with OpenCode free tier."
Write-Host "  Change providers in the Admin UI."
Write-Host ""
