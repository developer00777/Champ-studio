# Champ Studio desktop app (Windows)

Champ Studio as a normal Windows app: a Desktop icon that opens Champ Studio in its own window.
It still runs the same Docker container as `docker compose up`; the app sets that up and runs it
for you.

## Install

**From this repo:** double-click `desktop\windows\Install Champ Studio.cmd` (or run
`powershell -ExecutionPolicy Bypass -File desktop\windows\install.ps1`). Run it again after pulling
changes to update.

**For teammates:** run `powershell -ExecutionPolicy Bypass -File desktop\windows\make-release.ps1`
and send them `dist\ChampStudio-Windows-<version>.zip`. They unzip it and double-click
`Install Champ Studio.cmd` (if Windows asks whether to run it, choose Run).

Installing needs no administrator rights. It copies the app and the engine source to
`%LOCALAPPDATA%\Programs\Champ Studio`, adds **Champ Studio** to the Desktop and Start menu, adds an
entry under Settings > Apps (which also uninstalls it), and opens the app.

## What happens when you open it

A setup window shows four steps and walks through whatever is missing:

1. **Docker Desktop**: if it isn't installed, the app installs it with winget (about 600 MB).
   Windows asks for permission once; choose Yes.
2. **Docker engine**: installs WSL if needed (a restart may be required; the window says so and
   setup carries on the next time you open Champ Studio), then starts Docker Desktop and waits for it.
3. **Champ Studio engine**: builds the image from the bundled source, about 5 to 10 minutes the first
   time. It's rebuilt only after an update, and old images are removed.
4. **Start**: starts the container on `127.0.0.1:8787` (if another program holds that port, the next free one for that session)
   and waits until it answers.

Then Champ Studio opens in its own Edge window (Chrome if Edge is missing) with no tabs or address
bar, and the setup window goes away. Opening the app again later takes a few seconds.

Closing the Champ Studio window stops the engine to free memory. If an export or AI job is still
running, it waits for it to finish first (a notification says so). The icon by the clock has
**Open Champ Studio**, **Open in your browser**, **Show log** and **Quit Champ Studio**.

## Data and keys

- Projects, media and settings live in the Docker volume `champ_studio_champ-data`. It's the same
  compose project, container and volume as the repo's `docker-compose.yml`, so the desktop app and
  `docker compose up` share everything.
- API keys: add them in Settings inside the app. When installing from the repo, the installer also
  copies `.env` to `%LOCALAPPDATA%\ChampStudio\champstudio.env` and the app passes
  `OPENROUTER_API_KEY` / `MUAPI_API_KEY` from it to the container.
- The engine only listens on this PC (`127.0.0.1`), not on the network.

## Settings

`%LOCALAPPDATA%\ChampStudio\config.json`:

| Key | Default | Meaning |
|---|---|---|
| `port` | `8787` | Port on this PC. If another program holds it, the next free port is used for that session. |
| `stopEngineOnClose` | `true` | Stop the engine when the window closes. `false` keeps it running. |
| `browser` | `auto` | `auto` (Edge, then Chrome) or `chrome`. |

## Troubleshooting

- **Show details** in the setup window shows the live log. **Open log file** (or **Show log** in the
  tray menu) opens `%LOCALAPPDATA%\ChampStudio\logs\launcher.log`.
- `ChampStudio.ps1 -Check` prints what's installed and running without changing anything.
  `-NoWindow` runs the whole startup in a console; `-Stop` stops the engine.
- **Docker didn't start**: open Docker Desktop and read its message. Usually virtualization is off in
  the BIOS, or WSL needs `wsl --update`.

## Why PowerShell and not an .exe

Windows Smart App Control and company security tools block unsigned programs, so an unsigned
Electron or custom `.exe` won't start on many PCs (including ours). The app is a PowerShell script
(PowerShell is signed by Microsoft) with a WPF window. That needs no code-signing certificate and
works on any Windows 10/11 PC. It avoids anything security tools treat as suspicious: no hidden
consoles and no download-and-run (Docker Desktop comes from winget).

## Files

| File | What |
|---|---|
| `ChampStudio.ps1` | The app: single instance, setup window, tray icon, opens and watches the window |
| `lib/Engine.ps1` | Startup steps (Docker, WSL, image build with progress, container, health), jobs, stop |
| `lib/Window.ps1` | The setup window (WPF) |
| `lib/Common.ps1` | Paths, config, logging, child processes, source bundle and build hash |
| `compose.yml` | The container the app runs |
| `install.ps1` / `Uninstall.ps1` | Per-user install / uninstall (keeps your data unless you choose otherwise) |
| `make-release.ps1` | Builds the teammate zip |
| `tools/make-icon.ps1` | Regenerates `assets/champstudio.ico` from the brand mark |
