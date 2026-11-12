# Setup & Operations

## 1. Requirements

| Component | Minimum | Notes |
|---|---|---|
| OS | Windows 10/11 | Linux/macOS work for the core; Windows-specific tools degrade honestly |
| Python | 3.11 or 3.12 | 3.12 recommended |
| Disk | ~200 MB + models | Model weights are not committed |
| RAM | 8 GB | Keep the model budget ≤ 2 500 MB |
| Optional | Ollama, Tesseract, Android Studio | Features fail honestly when absent |

## 2. Install

```powershell
git clone https://github.com/ayaanraza2008/matrix-ai.git
cd matrix-ai
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pytest
```

`pip install -e .` is enough for normal use; `[dev]` adds pytest/ruff.

## 3. Run

```powershell
matrix serve
# or
python -m matrix_ai serve
```

Output:

```text
MATRIX 0.1.0 -> http://127.0.0.1:8765
Database: C:\Users\<you>\AppData\Local\MATRIX\matrix.db
Open the URL, paste the owner token once, and the dashboard will remember it locally.
```

The owner token is created on first run at `%LOCALAPPDATA%\MATRIX\owner_token.txt` (POSIX: `~/.matrix/`).
Set `MATRIX_OWNER_TOKEN` to supply your own. The database stores only its SHA-256 hash.

Other commands:

```powershell
matrix doctor      # health, hardware budget, tool + integration status (no secrets)
matrix backup      # verified SQLite backup under %LOCALAPPDATA%\MATRIX\backups
matrix pair-code   # one-time 6-digit code for the Android companion
matrix --version
```

## 4. Configuration

Copy `.env.example` to `.env` and load it in your shell, or set variables directly. Key settings:

| Variable | Default | Meaning |
|---|---|---|
| `MATRIX_HOME` | `%LOCALAPPDATA%\MATRIX` | DB, workspace, generated token |
| `MATRIX_HOST` / `MATRIX_PORT` | `127.0.0.1` / `8765` | Bind address. Keep loopback. |
| `MATRIX_FILE_ROOTS` | `<home>\workspace` | `;`-separated roots for file tools |
| `MATRIX_MODEL_PROVIDER` | `none` | `none` / `ollama` / `openai` |
| `MATRIX_MODEL_NAME` | `qwen2.5:0.5b-instruct-q4_K_M` | Model ID |
| `MATRIX_MODEL_RAM_BUDGET_MB` | `2500` | Model memory ceiling |
| `MATRIX_AUTONOMY_LEVEL` | `2` | L0–L4 |
| `MATRIX_APPROVAL_TTL_SECONDS` | `300` | Approval validity |
| `MATRIX_ALLOW_WEB_FETCH` | `true` | Public-page research tool |
| `MATRIX_ALLOW_CLOUD_MODELS` | `false` | Explicit opt-in for non-local endpoints |
| `MATRIX_STT_MODEL` / `MATRIX_STT_DEVICE` / `MATRIX_STT_COMPUTE_TYPE` | `tiny` / `cpu` / `int8` | Local faster-whisper configuration |
| `MATRIX_TTS_VOICE_ID` / `MATRIX_TTS_RATE` | OS default / `180` | Optional offline OS voice selection and speech rate |

### Offline speech (optional)

Install the optional local voice adapters:

```powershell
pip install -e ".[voice]"
```

- **Speech input:** MATRIX uses faster-whisper with a small multilingual tiny model, CPU inference and int8 compute by default. The first transcription may download the model weights into the local Hugging Face cache; after that, inference is local. No audio is uploaded to a cloud speech API or stored by MATRIX.
- **Speech output:** MATRIX uses pyttsx3 with voices already installed in Windows/OS. Available languages depend on the voices installed on the PC. The optional pyttsx3 dependency is MPL-2.0; faster-whisper is MIT. Piper is not bundled because the current Piper distribution is GPL-3.0-or-later, a different licensing choice for this MIT project.
- **Small-PC defaults:** MATRIX_STT_MODEL=tiny, MATRIX_STT_DEVICE=cpu, MATRIX_STT_COMPUTE_TYPE=int8, MATRIX_TTS_RATE=180. For a specific installed voice, set MATRIX_TTS_VOICE_ID to its voice ID or name.
- **Controls:** start a push-to-talk session, unmute before sending captured audio, mute immediately to block new capture, and use barge-in to interrupt speech. Driver support for immediate stop varies by operating system and voice.

The optional engines are lazy-loaded. Installing the extra does not force either model weights to download or start a microphone at MATRIX startup.

### Local model (optional, recommended)

```powershell
# Install Ollama for Windows from https://ollama.com, then:
ollama pull qwen2.5:0.5b-instruct-q4_K_M
$env:MATRIX_MODEL_PROVIDER = "ollama"
$env:MATRIX_MODEL_NAME = "qwen2.5:0.5b-instruct-q4_K_M"
matrix serve
```

Check `GET /api/models`: `status` should become `working`. If RAM is tight the manager reports
`unusable_for_model_mb` rather than swapping the machine.

Any OpenAI-compatible local server works too:

```powershell
$env:MATRIX_MODEL_PROVIDER = "openai"
$env:MATRIX_OPENAI_BASE_URL = "http://127.0.0.1:8080/v1"
```

### OCR

```powershell
winget install UB-Mannheim.TesseractOCR   # or the official installer
pip install -e ".[vision]"
```

Then `vision.ocr_image` works; `GET /api/vision/status` reports `ocr_available: true`.

## 5. Using the dashboard

1. Open `http://127.0.0.1:8765`.
2. Paste the owner token (stored in this browser only). "Lock dashboard" clears it.
3. **Dashboard** shows core health, model state, task counts and recent tasks.
4. **Chat** sends requests; deterministic intents run without a model.
5. Sensitive actions appear as an approval card with tool, risk, exact arguments and expiry.
6. **Tasks** shows steps, tool trace, evidence and the audit trail.
7. **Security** contains the emergency stop and policy summary.

## 6. Pairing the Android companion

```powershell
matrix pair-code
```

Enter the 6-digit code in the app (scaffold at `android/`). Codes expire after 5 minutes, are single-use,
and each device gets a revocable token. See `docs/ANDROID.md`.

## 7. Backup and restore

```powershell
matrix backup
# Backup: C:\Users\<you>\AppData\Local\MATRIX\backups\matrix-20261112T090000Z.db
# Integrity: ok
```

Restore: stop MATRIX, replace `matrix.db` with the backup (keep the WAL files out), start again. The backup
API produces a consistent snapshot even while the service is running.

## 8. Remote access (optional, advanced)

Do **not** port-forward the API. Prefer WireGuard/Tailscale and bind MATRIX to the private interface:

```powershell
$env:MATRIX_HOST = "100.x.y.z"   # Tailscale IP
matrix serve
```

Terminate TLS at a reverse proxy if the network is not already private. The app has no TLS listener.
Remote control only works while the PC is powered on, connected and running MATRIX.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `401 unauthenticated` | Missing/wrong token | Use the token file or `MATRIX_OWNER_TOKEN`; "Lock dashboard" then reconnect |
| Chat says no local model | Provider `none` or unavailable | Configure Ollama / local server; check `/api/models` |
| `403 scope_not_granted` | Task scopes narrower than tool | Grant the scope explicitly or use a broader request |
| `WAITING_FOR_USER` forever | Approval not decided | Open the task, approve/deny; approvals expire after TTL |
| OCR `503` | Tesseract missing | Install Tesseract + `[vision]` extra |
| Voice `503` | Optional local engine unavailable or failed | Install `.[voice]`; check the local Whisper model cache and OS voice pack |
| Port in use | Another instance | `matrix serve --port 8766` |
| Database locked | Second process | Only one MATRIX instance per home directory |

## 10. Security hygiene

- Keep `MATRIX_HOME` out of cloud-synced folders.
- Protect the token file; rotate it by deleting the file and restarting (or set a new env token).
- Revoke lost phones from **Devices** immediately.
- Review the audit trail after any autonomous task.
- Never commit `.env`, databases, model weights or keystores.


### Compare local model quality and speed

MATRIX includes a read-only benchmark command. It sends a fixed set of English and Hindi prompts to the configured local model, validates the returned plan against MATRIX tool schemas, and reports tool-selection accuracy, p50/p90 model latency, and observed process memory. It never executes generated plans, downloads models, or sends these benchmark prompts to a public cloud endpoint.

```powershell
# After Ollama is installed and the model is pulled:
matrix benchmark-model
```

The report separates schema-valid output from correct tool selection. A model can produce valid JSON and still choose the wrong tool. RSS for Ollama is an observed estimate based on visible local processes; it may not include shared GPU memory or a server running on another machine. Run the benchmark on the actual MATRIX PC before selecting a larger model.

MATRIX also supports a local OpenAI-compatible endpoint such as llama.cpp. Set MATRIX_MODEL_PROVIDER=openai, MATRIX_OPENAI_BASE_URL=http://127.0.0.1:8080/v1, and MATRIX_MODEL_NAME to the served model identifier. Non-local endpoints are refused by this benchmark even when cloud use is separately enabled.

