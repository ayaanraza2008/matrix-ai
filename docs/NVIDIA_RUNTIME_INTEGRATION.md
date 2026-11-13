# NVIDIA runtime integration contract

This document separates the two NVIDIA projects into their real runtime roles. It is intentionally explicit about what is connected today and what still needs implementation and hardware validation.

## Nemotron: reasoning provider

MATRIX already has an OpenAI-compatible model provider. A Nemotron checkpoint becomes usable by MATRIX when it is served by a local inference server that implements the OpenAI chat-completions and models endpoints.

Configure the server's actual URL and model identifier; do not assume a repository name is a model ID:

```powershell
$env:MATRIX_MODEL_PROVIDER = "openai"
$env:MATRIX_OPENAI_BASE_URL = "http://127.0.0.1:8000/v1"
$env:MATRIX_MODEL_NAME = "THE_MODEL_ID_EXPOSED_BY_YOUR_SERVER"
$env:MATRIX_ALLOW_CLOUD_MODELS = "false"
matrix serve
```

Then use the authenticated `GET /api/models` endpoint and run `matrix benchmark-model`. The health probe only establishes endpoint reachability; benchmark and policy tests are still required. MATRIX does not grant a model permission to execute tools. Planner output continues through the existing policy engine, exact-approval broker, typed tool registry and verification path.

### Hardware selection

Nemotron is a family, not one small model. The upstream repository describes Nano, Lightning, Super and Ultra tiers and multiple recipes/checkpoints. Choose a specific checkpoint and serving backend first. Do not infer that a 30B-class or larger checkpoint fits a low-memory Windows PC. Some workflows require datacenter-class GPUs and large system-memory/storage budgets. Training recipes are not a substitute for an inference deployment guide.

Official references:
- [Nemotron developer repository](https://github.com/NVIDIA-NeMo/Nemotron)
- [Nemotron usage cookbooks](https://github.com/NVIDIA-NeMo/Nemotron/tree/main/usage-cookbook)

## PersonaPlex: full-duplex voice provider

PersonaPlex is not an ordinary HTTP transcription endpoint. It is a full-duplex speech-to-speech service. The current upstream client uses a binary WebSocket protocol with typed message prefixes; the browser client and server negotiate a handshake, exchange Opus audio frames, and receive audio and text output.

The current MATRIX voice manager implements session lifecycle, mute, stop and barge-in controls, but it does not yet implement the audio codec, WebSocket transport, protocol state machine, resampling, or browser/device audio capture needed to connect PersonaPlex. The clone/install script deliberately does not claim to have completed that bridge.

A production bridge must, at minimum:

1. Use an authenticated MATRIX voice session and refuse microphone input while muted or stopped.
2. Connect only to an explicitly configured local/private PersonaPlex endpoint by default; never create a public tunnel or open firewall ports automatically.
3. Implement the upstream handshake and binary message types from the upstream client protocol.
4. Encode microphone audio to the expected Opus framing and decode returned audio safely; handle buffering, backpressure, reconnects, cancellation and barge-in.
5. Keep audio transient by default, enforce request/frame size limits, and avoid writing raw audio or credentials to logs.
6. Bind the voice session's persona and voice selection to owner-controlled settings, not model-generated tool instructions.
7. Test mute, stop, disconnect, malformed frames, oversized frames, concurrent sessions and emergency-stop behavior before enabling it by default.

Official references:
- [PersonaPlex repository and launch instructions](https://github.com/NVIDIA/personaplex)
- [PersonaPlex client protocol message types](https://github.com/NVIDIA/personaplex/blob/main/client/src/protocol/types.ts)

## Current integration status

| Capability | Status |
|---|---|
| Clone PersonaPlex and Nemotron sources | Implemented by `scripts/setup_nvidia_models.sh` |
| Install upstream dependencies in isolated environments | Implemented as an opt-in setup step; requires Linux/WSL2 prerequisites |
| Connect a served Nemotron checkpoint through MATRIX's existing OpenAI-compatible provider | Supported by existing provider; requires a chosen model/server and local configuration |
| Expose Nemotron as an automatically downloaded or automatically selected model | Not implemented; owner must choose checkpoint and backend |
| PersonaPlex full-duplex audio/WebSocket bridge | Not implemented |
| Hardware-backed GPU inference and end-to-end voice validation | Pending on the target machine |

No model is enabled merely because its source repository was cloned. No upstream process is launched automatically. This keeps MATRIX's normal startup deterministic and preserves the central policy boundary.
