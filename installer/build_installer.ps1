<#
.SYNOPSIS
    Builds the Free Claude Code Windows installer.
    Downloads dependencies (uv, nssm), prepares assets, and compiles the .exe.
.DESCRIPTION
    This script:
    1. Downloads uv.exe (if missing) https://astral.sh/uv/install.ps1
    2. Downloads nssm.exe (if missing) https://nssm.cc/release/nssm-2.24.zip
    3. Creates placeholder assets if originals are missing
    4. Compiles the Inno Setup .iss into a standalone installer EXE
    5. Outputs to the ./output/ directory
#>

param(
    [switch]$SkipDownload,
    [switch]$NoCompile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path "$PSScriptRoot\.."
$InstallerDir = $PSScriptRoot
$ToolsDir = Join-Path $InstallerDir "tools"
$AssetsDir = Join-Path $InstallerDir "assets"
$OutputDir = Join-Path $InstallerDir "output"
$ISCC = "C:\ProgramData\InnoSetup6\ISCC.exe"

Write-Host "========================================"
Write-Host "  Free Claude Code - Installer Builder"
Write-Host "========================================"
Write-Host ""
Write-Host "Project root: $ProjectRoot"
Write-Host "Installer dir: $InstallerDir"
Write-Host ""

# ── Create directories ───────────────────────────────────────────────────────
New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null
New-Item -ItemType Directory -Path $AssetsDir -Force | Out-Null
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

# ── Download uv ──────────────────────────────────────────────────────────────
$uvExe = Join-Path $ToolsDir "uv.exe"
if (-not (Test-Path $uvExe)) {
    if (-not $SkipDownload) {
        Write-Host "[1/3] Downloading uv..."
        $uvMsiPath = "$env:TEMP\uv-installer.msi"
        try {
            # Try to get uv from the local system first
            $localUv = Get-Command uv -ErrorAction SilentlyContinue
            if ($localUv) {
                Write-Host "  Found uv at $($localUv.Source), copying..."
                Copy-Item $localUv.Source $uvExe -Force
            } else {
                # Download the Windows MSI and extract uv.exe
                Write-Host "  Downloading uv via PowerShell installer..."
                $installScript = "$env:TEMP\uv-install.ps1"
                Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $installScript -UseBasicParsing

                # We just need uv.exe, run the installer then copy it
                $env:UV_INSTALL_DIR = "$env:TEMP\uv-install"
                Remove-Item -Path $env:UV_INSTALL_DIR -Recurse -Force -ErrorAction SilentlyContinue
                & powershell.exe -ExecutionPolicy Bypass -File $installScript 2>&1 | Out-Null

                $installedUv = Get-Command uv -ErrorAction SilentlyContinue
                if ($installedUv) {
                    Copy-Item $installedUv.Source $uvExe -Force
                    Write-Host "  [OK] uv.exe copied to $uvExe"
                } else {
                    # Try to find it after install
                    $uvPaths = @(
                        "$env:USERPROFILE\.local\bin\uv.exe",
                        "$env:USERPROFILE\.cargo\bin\uv.exe",
                        "$env:LOCALAPPDATA\uv\uv.exe"
                    )
                    $found = $false
                    foreach ($p in $uvPaths) {
                        if (Test-Path $p) {
                            Copy-Item $p $uvExe -Force
                            Write-Host "  [OK] uv.exe found at $p"
                            $found = $true
                            break
                        }
                    }
                    if (-not $found) {
                        Write-Warning "  Could not find uv.exe. Try running: irm https://astral.sh/uv/install.ps1 | iex"
                        Write-Warning "  Then re-run this build script."
                    }
                }
            }
        }
        catch {
            Write-Warning "  Failed to get uv.exe: $_"
            Write-Warning "  Download manually from https://github.com/astral-sh/uv/releases"
        }
    } else {
        Write-Host "[1/3] Skipping uv download (--SkipDownload)"
    }
} else {
    Write-Host "[1/3] uv.exe already present at $uvExe"
}

# ── Download nssm ────────────────────────────────────────────────────────────
$nssmExe = Join-Path $ToolsDir "nssm.exe"
if (-not (Test-Path $nssmExe)) {
    if (-not $SkipDownload) {
        Write-Host "[2/3] Downloading nssm (Non-Sucking Service Manager)..."
        try {
            $nssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
            $zipPath = "$env:TEMP\nssm-2.24.zip"
            $extractPath = "$env:TEMP\nssm-extract"

            Remove-Item -Path $zipPath -Force -ErrorAction SilentlyContinue
            Remove-Item -Path $extractPath -Recurse -Force -ErrorAction SilentlyContinue

            Write-Host "  Downloading from $nssmUrl..."
            Invoke-WebRequest -Uri $nssmUrl -OutFile $zipPath -UseBasicParsing

            Write-Host "  Extracting..."
            Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

            $nssm64 = Join-Path $extractPath "nssm-2.24\win64\nssm.exe"
            if (Test-Path $nssm64) {
                Copy-Item $nssm64 $nssmExe -Force
                Write-Host "  [OK] nssm.exe copied to $nssmExe"
            } else {
                Write-Warning "  nssm.exe not found in extracted archive"
            }

            # Cleanup
            Remove-Item -Path $zipPath -Force -ErrorAction SilentlyContinue
            Remove-Item -Path $extractPath -Recurse -Force -ErrorAction SilentlyContinue
        }
        catch {
            Write-Warning "  Failed to download nssm: $_"
            Write-Warning "  Download manually from https://nssm.cc/download"
        }
    } else {
        Write-Host "[2/3] Skipping nssm download (--SkipDownload)"
    }
} else {
    Write-Host "[2/3] nssm.exe already present at $nssmExe"
}

# ── Prepare assets ───────────────────────────────────────────────────────────
Write-Host "[3/3] Preparing assets..."

# Check for icon
$sourceIcon = Join-Path $ProjectRoot "assets\icon.ico"
$targetIcon = Join-Path $AssetsDir "icon.ico"
if (Test-Path $sourceIcon) {
    Copy-Item $sourceIcon $targetIcon -Force
    Write-Host "  [OK] Icon copied from project assets"
} elseif (-not (Test-Path $targetIcon)) {
    # Create a default icon placeholder - Inno Setup needs a real .ico
    # Use the first .ico found or create from Python
    Write-Host "  Creating default icon..."
    try {
        # Create a simple 32x32 icon using base64 (generated from a minimal valid .ico)
        # This is a valid 1x1 transparent ICO file
        $icoBytes = [Convert]::FromBase64String("AAABAAEAEBAAAAEAIABoBAAAFgAAACgAAAAQAAAAIAAAAAEAIAAAAAAAAAAAABMLAAATCwAAAAAAAAAAAABmZmYAZmZmAGZmZgBmZmYAZmZmAGZmZgBmZmYAZmZmAGZmZgBmZmYAZmZmAGZmZgBmZmYAZmZmAGZmZgBmZmYAZmZmAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAAAAAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAAAAAAAAAAAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAAAAAAAAAAAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAAAAAAAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAAAAAAAAAAAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAAAAAAAAAAAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAAAAAAAAAAAAAAAAAAAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAAAAAAAAAAAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAAAAAAAAAAAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAAAAAAAAAAAAAAAAAAAAAAAAAAAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        [System.IO.File]::WriteAllBytes($targetIcon, $icoBytes)
        Write-Host "  [OK] Placeholder icon created"
    }
    catch {
        Write-Warning "  Could not create icon file. Create a 32x32 .ico at: $targetIcon"
    }
}

# Check for wizard images (optional - skip if missing)
$wizardBmp = Join-Path $AssetsDir "wizard.bmp"
$wizardSmallBmp = Join-Path $AssetsDir "wizard-small.bmp"
if (-not (Test-Path $wizardBmp)) {
    Write-Host "  [INFO] wizard.bmp not found - using Inno Setup defaults"
}
if (-not (Test-Path $wizardSmallBmp)) {
    Write-Host "  [INFO] wizard-small.bmp not found - using Inno Setup defaults"
}

Write-Host ""
Write-Host "=== Build Summary ==="
$uvExists = Test-Path $uvExe
$nssmExists = Test-Path $nssmExe
$iconExists = Test-Path $targetIcon

Write-Host "  uv.exe:     $(if($uvExists){'[OK]'}else{'[MISSING]'})"
Write-Host "  nssm.exe:   $(if($nssmExists){'[OK]'}else{'[MISSING]'})"
Write-Host "  icon.ico:   $(if($iconExists){'[OK]'}else{'[MISSING]'})"

# ── Compile Inno Setup ──────────────────────────────────────────────────────
if (-not $NoCompile) {
    Write-Host ""
    Write-Host "Compiling installer..."

    if (-not (Test-Path $ISCC)) {
        Write-Error "ISCC.exe not found at $ISCC. Install Inno Setup 6 first."
        exit 1
    }

    if (-not $uvExists) {
        Write-Error "uv.exe is required. Run without -SkipDownload or place it at: $uvExe"
        exit 1
    }

    if (-not $nssmExists) {
        Write-Error "nssm.exe is required. Run without -SkipDownload or place it at: $nssmExe"
        exit 1
    }

    $issFile = Join-Path $InstallerDir "fcc_setup.iss"
    Write-Host "  Script: $issFile"
    Write-Host "  Output: $OutputDir"
    Write-Host ""

    Push-Location $InstallerDir
    try {
        & $ISCC $issFile 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "=== BUILD SUCCESSFUL ==="
            Write-Host ""
            Get-ChildItem $OutputDir -Filter "*.exe" | ForEach-Object {
                Write-Host "Installer created: $($_.FullName)"
                Write-Host "Size: $('{0:N0}' -f ($_.Length / 1KB)) KB"
            }
        } else {
            Write-Error "Inno Setup compilation failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
} else {
    Write-Host ""
    Write-Host "Skipping compilation (--NoCompile)"
    Write-Host "Run the build script again without --NoCompile to compile."
    Write-Host "Or compile manually:"
    Write-Host "  `"$ISCC`" `"$InstallerDir\fcc_setup.iss`""
}

Write-Host ""
Write-Host "Done!"
