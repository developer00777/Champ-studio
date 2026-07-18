# Champ Studio on Windows

Two ways — pick one.

## A. Simple (no Docker) — any Windows 10/11 machine

1. Get this folder onto the machine (zip from Deep, or `git clone`).
2. Right-click `windows/setup-windows.ps1` → **Run with PowerShell** (one-time, ~2 min).
3. Double-click `windows/run-windows.bat` — your browser opens at
   `http://127.0.0.1:8787`.
4. Settings → paste your OpenRouter key → create.

Everything (Python, dependencies) is installed per-user by `uv`; no admin rights,
nothing touches system Python. Assets live in `%USERPROFILE%\.champstudio`.

## B. Docker (identical to the server/Mac container)

With Docker Desktop installed:

```powershell
docker compose up -d --build     # from the repo root
# → http://localhost:8787
```

## C. No install at all

If Deep runs the team server (see `docs/DEPLOY-SERVER.md`), just open
`http://<server>:8787` in any browser — nothing to install.
