#!/usr/bin/env bash
# Clone and optionally install NVIDIA PersonaPlex + Nemotron in isolated folders.
# Run inside Ubuntu 22.04/24.04 or WSL2 Ubuntu. Never installs into MATRIX's venv.
set -euo pipefail

ROOT="${MATRIX_HOME:-${HOME}/.matrix}/upstream"
INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --install) INSTALL=1 ;;
    -h|--help)
      echo "Usage: $0 [--install]"
      echo "Default: clone both repositories only."
      echo "--install: install PersonaPlex and Nemotron into isolated environments."
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
mkdir -p "$ROOT"

clone_or_update() {
  local url="$1" dir="$2"
  if [ -d "$dir/.git" ]; then
    echo "Already cloned: $dir (leaving local changes untouched)"
  elif [ -e "$dir" ]; then
    echo "Refusing to overwrite non-git path: $dir" >&2
    exit 1
  else
    git clone --depth 1 "$url" "$dir"
  fi
}

clone_or_update https://github.com/NVIDIA/personaplex.git "$ROOT/personaplex"
clone_or_update https://github.com/NVIDIA-NeMo/Nemotron.git "$ROOT/Nemotron"

if [ "$INSTALL" -eq 0 ]; then
  echo "Sources cloned under $ROOT. Re-run with --install to create isolated runtimes."
  exit 0
fi

command -v python3.12 >/dev/null || { echo "Python 3.12 is required for the Nemotron setup." >&2; exit 1; }
command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/" >&2; exit 1; }

if ! pkg-config --exists opus 2>/dev/null; then
  echo "Missing Opus development library. Install with: sudo apt-get update && sudo apt-get install -y libopus-dev"
  exit 1
fi

# PersonaPlex uses a dedicated venv; GPU-enabled PyTorch must match the host CUDA driver.
python3.12 -m venv "$ROOT/personaplex/.venv"
"$ROOT/personaplex/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/personaplex/.venv/bin/pip" install "$ROOT/personaplex/moshi/"

# Nemotron owns its uv-managed workspace; do not mix it with the PersonaPlex or MATRIX venv.
(
  cd "$ROOT/Nemotron"
  uv sync --python 3.12
)

cat <<'EOF'
Runtime dependencies installed in isolated environments.
Next steps:
  1. Accept the PersonaPlex model licence on its Hugging Face model page.
  2. Export HF_TOKEN in the shell that launches PersonaPlex (never commit it).
  3. Confirm a supported NVIDIA GPU, driver, CUDA/PyTorch compatibility and VRAM.
  4. Review Nemotron's recipe-specific GPU/storage requirements before running a pipeline.
This script does not download model weights, start servers, expose ports, or change MATRIX configuration.
EOF
