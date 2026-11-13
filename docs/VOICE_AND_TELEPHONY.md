# Free local voice and phone roadmap

MATRIX's voice session API now has real optional offline speech adapters:

- **STT:** faster-whisper, CPU + INT8 by default; `tiny` is the low-memory multilingual default.
- **TTS:** Piper CLI with a user-downloaded local ONNX voice; playback uses the host's default audio device via sounddevice. On Windows, `MATRIX_TTS_PROVIDER=windows-sapi` opts in to the locally installed SAPI voice instead (no extra model download); `MATRIX_TTS_PROVIDER=none` disables speech output entirely.
- Audio is decoded in memory, written only to a short-lived temporary file for Whisper, and removed after inference. No raw audio is stored in SQLite.
- Mute is checked before decoding/inference. Stop, mute and barge-in cancel active speech playback and call the engine's native `stop()` where available (Windows SAPI terminates the synthesis process immediately).
- Text is screened before speaking: `POST /api/voice/check-speech` runs the backend credential detector so callers can preflight text before local or browser playback.

## Install (Windows PowerShell)

```powershell
pip install -e ".[voice]"
# Download a Piper voice model (.onnx plus its .onnx.json sidecar) from the official Piper voices catalogue.
$env:MATRIX_PIPER_MODEL = "C:\MATRIX\voices\en_US-lessac-medium.onnx"
$env:MATRIX_STT_MODEL = "tiny"
$env:MATRIX_STT_DEVICE = "cpu"
$env:MATRIX_STT_COMPUTE_TYPE = "int8"
matrix serve
```

The first faster-whisper model load may download model weights into the Hugging Face cache. For a fully offline deployment, pre-cache the selected model before disconnecting from the network. Piper voices are not downloaded by MATRIX; choose and install one yourself, then point `MATRIX_PIPER_MODEL` at it.

## Windows SAPI speech output (opt-in)

On Windows 10/11 with an installed speech voice, MATRIX can speak through the built-in Windows SAPI engine without downloading anything:

```powershell
$env:MATRIX_TTS_PROVIDER = "windows-sapi"
matrix serve
```

Text is passed to a fixed PowerShell script over stdin as JSON; it is never interpolated into a command string or written to disk. Speech runs as a hidden, non-interactive process with a 180-second timeout; barge-in terminates the synthesis process immediately. If no local speech voice is installed, the engine reports it instead of failing silently. This provider is strictly opt-in and only active on `win32`.

## HTTP flow

1. Authenticated client calls `POST /api/voice/start`.
2. Client records a short WebM/Opus clip and sends base64 to `POST /api/voice/transcribe`.
3. MATRIX returns transcript, detected language and provider; the client submits the transcript through the existing `POST /api/chat` task pipeline.
4. Once the task is complete, the client calls `POST /api/voice/speak` with the assistant reply.
5. `POST /api/voice/barge-in`, `/mute` or `/stop` interrupts playback.

The API does not bypass the task orchestrator: transcripts are ordinary user requests and all computer/business actions still pass through typed tools, scopes, policy, exact approvals and verification.

## Phone calling is a separate transport

Free, self-hosted speech and AI do **not** make PSTN phone calls free. Phone numbers, carrier minutes, regulatory registration and inbound/outbound routing may incur charges. MATRIX will keep SIP/PSTN behind an optional adapter; the first no-recurring-cost target is local browser/desktop voice and LAN/WebRTC calling. Do not expose the local API to the public internet or port-forward it; use private networking such as Tailscale/WireGuard.

## Remaining production gates

- Add WebRTC/SIP transport and call lifecycle management, with no PSTN claims until a provider is configured.
- Add local audio-device selection, echo cancellation, VAD and streaming partial transcripts.
- Add browser/desktop automation adapters through existing typed-tool policy and verification boundaries.
- Validate real microphone/speaker end-to-end on the target Windows machine and Android build on a physical device.
- Add load, cancellation, privacy, dependency and supply-chain checks before declaring the voice/telephony milestone complete.
