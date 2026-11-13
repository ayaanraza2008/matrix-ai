# NVIDIA PersonaPlex + Nemotron setup

This is an optional, isolated upstream-model setup for MATRIX. The upstream repositories are cloned under
`$MATRIX_HOME/upstream` (default: `~/.matrix/upstream`). They are **not** vendored into MATRIX and their
dependencies must never be installed into MATRIX's Python environment.

## Roles in MATRIX

| Upstream | Intended capability | Integration boundary |
|---|---|---|
| [NVIDIA/personaplex](https://github.com/NVIDIA/personaplex) | Full-duplex speech-to-speech, voice/persona conditioning | Separate voice service. MATRIX's current voice manager provides session controls but does not yet implement a PersonaPlex WebSocket/audio adapter. |
| [NVIDIA-NeMo/Nemotron](https://github.com/NVIDIA-NeMo/Nemotron) | Model family, reasoning/tool-use recipes, fine-tuning/evaluation and deployment examples | Use a selected Nemotron checkpoint through a local inference server with an OpenAI-compatible API, then configure MATRIX's existing local provider. The repo itself is a developer asset hub, not one single model binary. |

MATRIX's policy engine remains authoritative: model output is a proposal, not permission to execute tools. All actions must
continue through the existing typed tools, risk checks, exact approvals and verification. Keep inference local unless the
owner explicitly enables cloud models.

## Requirements and constraints

### PersonaPlex

- Official project: [NVIDIA/personaplex](https://github.com/NVIDIA/personaplex).
- Real-time full-duplex speech-to-speech model based on the Moshi architecture; the model weights are gated by the
  NVIDIA Open Model License.
- Use Ubuntu 22.04/24.04 or Ubuntu under WSL2 with a correctly configured NVIDIA GPU/driver stack. Native Windows
  installation is not the supported path documented by the upstream README.
- Install the Opus development library (`sudo apt-get install libopus-dev`) and Python 3.12.
- Accept the model licence on Hugging Face and authenticate with `HF_TOKEN` at runtime. Never place the token in
  source control, logs, issue reports, or this repository.
- The upstream README documents `pip install moshi/.` and `python -m moshi.server --ssl <directory>`.
  CPU offload is available with the upstream `accelerate` extra, but it is not a substitute for checking performance.
- Select a PyTorch/CUDA wheel compatible with the actual GPU and driver; Blackwell systems may need the upstream
  CUDA 13.0 wheel instructions.

### Nemotron

- Official project: [NVIDIA-NeMo/Nemotron](https://github.com/NVIDIA-NeMo/Nemotron).
- Requires `uv` and Python 3.12 for the documented repo setup; use `uv sync --python 3.12` from the checkout.
- Hardware is recipe-specific. Some GPU inference/training stages call for at least 80 GB VRAM, 128 GB system RAM
  recommended, and around 50 GB free disk. Stage 0 data generation can use NVIDIA-hosted APIs and does not itself
  require a local GPU; that is not the same as local model inference.
- NVIDIA API credentials are optional and only needed for workflows that use the hosted NVIDIA endpoint. Keep
  `NVIDIA_API_KEY` in the runtime environment, never in committed files.
- Choose a concrete Nano/Lightning/Super/Ultra checkpoint and serving backend before configuring MATRIX. Do not
  assume the entire Nemotron family fits an 8 GB PC.

## Clone both repositories

From Ubuntu/WSL2:

```bash
bash scripts/setup_nvidia_models.sh
```

This clones sources only and does not run upstream install scripts or download model weights.

To install the isolated upstream Python environments:

```bash
sudo apt-get update
sudo apt-get install -y git libopus-dev pkg-config
# Install uv using its official installer if it is not already available.
bash scripts/setup_nvidia_models.sh --install
```

The script creates PersonaPlex's own `.venv` and lets Nemotron manage its own uv workspace. It does not configure
credentials, download model weights, launch servers, open firewall ports, or modify MATRIX's provider settings.

## Start PersonaPlex manually

After accepting the model licence and exporting the Hugging Face token in the same shell:

```bash
cd "${MATRIX_HOME:-$HOME/.matrix}/upstream/personaplex"
export HF_TOKEN="your-token-from-hugging-face"
source .venv/bin/activate
SSL_DIR="$(mktemp -d)"
python -m moshi.server --ssl "$SSL_DIR"
```

The upstream web UI is normally served on port 8998. Keep it bound to a trusted local/private network and do not
port-forward it publicly. MATRIX does not yet proxy this server or translate its audio stream into MATRIX voice sessions.

## Use a served Nemotron model with MATRIX

After selecting and deploying a checkpoint using the official Nemotron usage cookbook and a local serving backend,
configure the OpenAI-compatible endpoint. Example only; the port and model ID must match the actual server:

```powershell
$env:MATRIX_MODEL_PROVIDER = "openai"
$env:MATRIX_OPENAI_BASE_URL = "http://127.0.0.1:8000/v1"
$env:MATRIX_MODEL_NAME = "YOUR_SERVED_NEMOTRON_MODEL_ID"
$env:MATRIX_ALLOW_CLOUD_MODELS = "false"
matrix serve
```

Then check the authenticated `GET /api/models` status and run MATRIX's read-only model benchmark. A successful HTTP
health check alone does not prove correct tool selection or safe agent behavior.

## Completion boundary

The repository integration is prepared by this setup guide and clone/install helper. A live installation is only
complete after the target PC has the required GPU/driver/software, both upstream environments install successfully,
the gated PersonaPlex licence and credentials are supplied by the owner, the selected Nemotron checkpoint is served,
and MATRIX passes voice/model integration tests. The current MATRIX code has no PersonaPlex audio adapter; that
requires a separate implementation and hardware-backed end-to-end validation.
