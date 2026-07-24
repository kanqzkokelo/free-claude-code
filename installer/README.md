# Free Claude Code - Windows Installer Builder

This directory contains everything needed to build a standalone Windows installer (.exe) for Free Claude Code.

## Quick Start

```powershell
# From this directory:
.\build_installer.ps1
```

This will:
1. Download `uv.exe` (Python package manager)
2. Download `nssm.exe` (Windows Service Manager)
3. Compile the Inno Setup script into `output/FreeClaudeCode-Setup-1.2.41.exe`

## Prerequisites

- **Windows 10/11** (64-bit)
- The build script downloads everything else automatically

## What the Installer Does

The resulting `.exe` installer:

1. **Installs Free Claude Code** to `C:\Program Files\FCC\`
2. **Downloads Python 3.14** (via uv) and creates a virtual environment
3. **Installs all dependencies** into the venv
4. **Installs Claude Code CLI** via npm (if Node.js is detected)
5. **Sets system environment variables** so `claude` works in any terminal:
   - `ANTHROPIC_BASE_URL=http://localhost:8082`
   - `ANTHROPIC_AUTH_TOKEN=freecc`
   - `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`
   - `CLAUDE_CODE_AUTO_COMPACT_WINDOW=190000`
6. **Creates a Windows service** "Free Claude Code Proxy" that auto-starts with Windows
7. **Pre-configures** with OpenCode free-tier API key (`public`) and free model
8. **Creates Start Menu and Desktop shortcuts**

## Files

| File | Purpose |
|------|---------|
| `fcc_setup.iss` | Inno Setup script - defines the installer |
| `build_installer.ps1` | Build orchestrator - downloads deps, compiles |
| `install_config.ps1` | Post-install configuration (env vars, service) |
| `uninstall_config.ps1` | Pre-uninstall cleanup (stop service, remove env) |
| `fcc-service-wrapper.py` | Python service entry point |
| `preconfigured.env` | Pre-configured .env with OpenCode free tier |
| `start_fcc_admin.bat` | Shortcut to open Admin UI |
| `check_nodejs.cmd` | Node.js detection script |
| `tools/` | Bundled binaries (uv.exe, nssm.exe) |
| `assets/` | Installer icons and images |
| `output/` | Built installer .exe files |

## Rebuilding

After making changes to the project source code:

```powershell
.\build_installer.ps1
```

The build script will use the current project source (one directory up).

## Manual Compilation

If you have Inno Setup 6 installed:

```powershell
iscc fcc_setup.iss
```

## Architecture

```
User downloads FreeClaudeCode-Setup.exe
  │
  ▼
Inno Setup extracts files to C:\Program Files\FCC\
  │
  ▼
PowerShell post-install script:
  ├── Downloads Python 3.14 via uv
  ├── Creates virtual environment
  ├── Installs free-claude-code and dependencies
  ├── Installs Claude Code CLI (npm install -g)
  ├── Sets system environment variables
  └── Creates Windows service via nssm
  │
  ▼
User can now type "claude" in any terminal
Proxy auto-starts with Windows (service)
```

## Service Management

```powershell
# Check service status
net start FCCProxy

# Stop the service
net stop FCCProxy

# View logs
Get-Content "C:\Program Files\FCC\logs\fcc-service.log" -Tail 50
```

## License

MIT - see the project root LICENSE file.
