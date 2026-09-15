#!/usr/bin/env bash
# ==============================================================================
# Autonomous Multi-Niche AI Video & Cinema Engine — 1-Click Installer
# ==============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo -e "${CYAN}${BOLD}"
cat << "EOF"
  _  _ ___ ___  ___ ___     ___ _  _ ___ _  _ ___ 
 | \| |_ _| __|/ _ \ __|   / __| || |_ _| \| | __|
 | .` || || _| | (_) |__ \  | (__| __ || || .` | _| 
 |_|\_|___|___| \___/___/   \___|_||_|___|_|\_|___|
   Autonomous Multi-Niche AI Video & Cinema Engine
   • 2-Tier Director + Scriptwriter Hybrid Pipeline
   • ComfyUI SDXL Hero Scene Art & Viral Thumbnails
   • Word-Synchronized Fitted Pill Karaoke Subtitles
   • Hardware-Accelerated NVENC Video Compositing
EOF
echo -e "${NC}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

AUTO_YES=false
for arg in "$@"; do
    if [ "$arg" == "-h" ] || [ "$arg" == "--help" ]; then
        echo "Usage: ./install.sh [OPTIONS]"
        echo ""
        echo "Options:"
        echo "  -y, --yes    Run installation non-interactively using default settings"
        echo "  -h, --help   Display this help message and exit"
        exit 0
    fi
    if [ "$arg" == "-y" ] || [ "$arg" == "--yes" ]; then
        AUTO_YES=true
    fi
done

# ── 1. Check System Prerequisites ─────────────────────────────────────────────
log_info "Step 1/6: Checking system prerequisites..."

check_cmd() {
    command -v "$1" >/dev/null 2>&1
}

MISSING_PKGS=()
for cmd in curl git python3 ffmpeg; do
    if ! check_cmd "$cmd"; then
        MISSING_PKGS+=("$cmd")
    fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    log_warn "Missing required system tools: ${MISSING_PKGS[*]}"
    INSTALL_SYS=false
    if [ "$AUTO_YES" = true ]; then
        INSTALL_SYS=true
    else
        read -p "Would you like to auto-install missing packages using your system package manager? (y/N): " RESP
        [[ "$RESP" =~ ^[Yy]$ ]] && INSTALL_SYS=true
    fi

    if [ "$INSTALL_SYS" = true ]; then
        if check_cmd apt-get; then
            log_info "Installing packages via apt..."
            sudo apt-get update -y && sudo apt-get install -y "${MISSING_PKGS[@]}" python3-venv
        elif check_cmd pacman; then
            log_info "Installing packages via pacman..."
            sudo pacman -Sy --noconfirm "${MISSING_PKGS[@]}"
        elif check_cmd dnf; then
            log_info "Installing packages via dnf..."
            sudo dnf install -y "${MISSING_PKGS[@]}"
        elif check_cmd brew; then
            log_info "Installing packages via Homebrew..."
            brew install "${MISSING_PKGS[@]}"
        else
            log_error "Could not detect package manager. Please install: ${MISSING_PKGS[*]}"
            exit 1
        fi
    else
        log_error "Please install missing packages (${MISSING_PKGS[*]}) and re-run ./install.sh"
        exit 1
    fi
fi

PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
log_success "System prerequisites verified: Python $PYTHON_VER, FFmpeg present."

# Check NVIDIA GPU & NVENC support
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
    GPU_VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader | head -1)
    log_success "NVIDIA GPU detected: ${BOLD}${GPU_NAME}${NC} (${GPU_VRAM})"
    if ffmpeg -encoders 2>/dev/null | grep -q h264_nvenc; then
        log_success "Hardware-accelerated NVENC encoder verified (h264_nvenc ready)."
    else
        log_warn "NVENC not detected in FFmpeg build. Pipeline will fallback to CPU rendering."
    fi
else
    log_warn "No NVIDIA GPU detected. Pipeline will run in CPU-only mode."
fi

# ── 2. Setup / Verify Ollama & AI Director ─────────────────────────────────────
log_info "Step 2/6: Checking AI Director LLM backend (Cloud API vs. Local Ollama)..."

if ! command -v ollama >/dev/null 2>&1; then
    log_info "Ollama is not installed. (Note: The AI Director uses your primary cloud LLM like DeepSeek/OpenAI by default)."
    INSTALL_OLLAMA=false
    if [ "$AUTO_YES" = true ]; then
        INSTALL_OLLAMA=false
    else
        read -p "Would you like to install local Ollama for offline execution? (y/N): " RESP
        [[ "$RESP" =~ ^[Yy]$ ]] && INSTALL_OLLAMA=true
    fi

    if [ "$INSTALL_OLLAMA" = true ]; then
        log_info "Installing Ollama via official installer..."
        curl -fsSL https://ollama.com/install.sh | sh
    else
        log_info "Skipping Ollama. The pipeline will direct shots via your configured cloud LLM in config.toml."
    fi
fi

if command -v ollama >/dev/null 2>&1; then
    log_success "Ollama is installed."

    # Ensure Ollama daemon is active
    if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        log_info "Starting Ollama service in the background..."
        ollama serve >/dev/null 2>&1 &
        sleep 3
    fi

    # Check for fast storyboard director model
    INSTALLED_MODELS=$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}')
    if echo "$INSTALLED_MODELS" | grep -q "qwen3:8b"; then
        log_success "Local Movie Director fallback model 'qwen3:8b' is ready."
    elif echo "$INSTALLED_MODELS" | grep -qE "qwen.*8b|qwen2\.5"; then
        FOUND_QWEN=$(echo "$INSTALLED_MODELS" | grep -E "qwen" | head -1)
        log_success "Found compatible local Qwen model: '$FOUND_QWEN'"
    else
        log_info "Pulling optional local Director storyboard model 'qwen3:8b'..."
        ollama pull qwen3:8b || log_warn "Could not auto-pull qwen3:8b. You can pull it later via: ollama pull qwen3:8b"
    fi
fi

# ── 3. Setup / Verify ComfyUI SDXL ────────────────────────────────────────────
log_info "Step 3/6: Checking ComfyUI SDXL integration..."

DEFAULT_COMFY="${HOME}/comfyui/ComfyUI"
if [ -d "$DEFAULT_COMFY" ]; then
    log_success "Detected ComfyUI installation at: $DEFAULT_COMFY"
elif [ -n "$COMFY_PATH" ] && [ -d "$COMFY_PATH" ]; then
    log_success "Detected ComfyUI via COMFY_PATH at: $COMFY_PATH"
else
    log_warn "ComfyUI was not detected at default path (${DEFAULT_COMFY})."
    CLONE_COMFY=false
    if [ "$AUTO_YES" = true ]; then
        CLONE_COMFY=false
    else
        read -p "Would you like to auto-clone ComfyUI into ~/comfyui/ComfyUI now? (y/N): " RESP
        [[ "$RESP" =~ ^[Yy]$ ]] && CLONE_COMFY=true
    fi

    if [ "$CLONE_COMFY" = true ]; then
        mkdir -p "${HOME}/comfyui"
        log_info "Cloning ComfyUI repository..."
        git clone https://github.com/comfyanonymous/ComfyUI.git "$DEFAULT_COMFY"
        log_success "ComfyUI cloned into $DEFAULT_COMFY."
        log_warn "Download your SDXL model (e.g. juggernautXL_ragnarok.safetensors) into $DEFAULT_COMFY/models/checkpoints/"
    else
        log_info "To enable AI Hero Scene art & viral thumbnails later, clone ComfyUI into ~/comfyui/ComfyUI or export COMFY_PATH."
    fi
fi

# ── 4. Setup Python Virtual Environment ───────────────────────────────────────
log_info "Step 4/6: Setting up Python virtual environment and dependencies..."

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    log_success "Created Python virtual environment (./.venv)."
else
    log_info "Virtual environment already exists (./.venv)."
fi

if [ -x "$(command -v uv)" ]; then
    log_info "Using fast package installer (uv)..."
    uv pip install --upgrade pip -q 2>/dev/null || true
    log_info "Installing core engine dependencies from requirements.txt..."
    uv pip install -r requirements.txt -q
    log_success "Python dependencies verified via uv."
elif [ -f ".venv/bin/pip" ]; then
    .venv/bin/pip install --upgrade pip -q
    log_info "Installing core engine dependencies from requirements.txt..."
    .venv/bin/pip install -r requirements.txt -q
    log_success "Python dependencies verified via pip."
else
    .venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1 || true
    .venv/bin/python -m pip install -r requirements.txt -q
    log_success "Python dependencies verified."
fi

# ── 5. Setup Configuration ────────────────────────────────────────────────────
log_info "Step 5/6: Configuring engine settings..."

if [ ! -f "config.toml" ]; then
    if [ -f "config.example.toml" ]; then
        cp config.example.toml config.toml
        log_success "Created config.toml from config.example.toml."
        log_warn "IMPORTANT: Edit config.toml to add your LLM API key (DeepSeek / OpenAI) and Pexels API key."
    fi
else
    log_info "config.toml already exists. Preserving your existing configuration."
fi

# ── 6. Setup Global CLI Commands ──────────────────────────────────────────────
log_info "Step 6/6: Installing global CLI commands ('moneyprinter-studio', 'cinema-engine', 'run_worker')..."

chmod +x "${PROJECT_DIR}/run_worker.sh"
chmod +x "${PROJECT_DIR}/run_series.py"
chmod +x "${PROJECT_DIR}/run_infinite_generator.py"

INSTALL_GLOBAL=false
if [ -d "$HOME/.local/bin" ]; then
    ln -sf "${PROJECT_DIR}/run_worker.sh" "$HOME/.local/bin/moneyprinter-studio"
    ln -sf "${PROJECT_DIR}/run_worker.sh" "$HOME/.local/bin/moneyprinter"
    ln -sf "${PROJECT_DIR}/run_worker.sh" "$HOME/.local/bin/cinema-engine"
    ln -sf "${PROJECT_DIR}/run_worker.sh" "$HOME/.local/bin/run_worker"
    INSTALL_GLOBAL=true
elif [ -d "/usr/local/bin" ] && [ -w "/usr/local/bin" ]; then
    ln -sf "${PROJECT_DIR}/run_worker.sh" "/usr/local/bin/moneyprinter-studio"
    ln -sf "${PROJECT_DIR}/run_worker.sh" "/usr/local/bin/moneyprinter"
    ln -sf "${PROJECT_DIR}/run_worker.sh" "/usr/local/bin/cinema-engine"
    ln -sf "${PROJECT_DIR}/run_worker.sh" "/usr/local/bin/run_worker"
    INSTALL_GLOBAL=true
fi

if [ "$INSTALL_GLOBAL" = true ]; then
    log_success "Installed global commands: 'moneyprinter-studio', 'moneyprinter', 'cinema-engine' and 'run_worker'"
else
    log_warn "Could not link to ~/.local/bin. You can run './run_worker.sh' directly."
fi

# ── Finished ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}================================================================${NC}"
echo -e "${GREEN}${BOLD}   🎬 Installation Complete! MoneyPrinter Studio is Ready!      ${NC}"
echo -e "${GREEN}${BOLD}================================================================${NC}"
echo ""
echo -e "Quickstart Options:"
echo -e "  1. Launch a 5-episode narrative series for any niche:"
echo -e "     ${CYAN}./run_worker.sh series prehistoric 5${NC}      # Primordial beasts & extinctions"
echo -e "     ${CYAN}./run_worker.sh series space_anomalies 5${NC}  # Deep space anomalies"
echo -e "     ${CYAN}./run_worker.sh series true_crime 5${NC}       # Unsolved true crime cold cases"
echo -e "     ${CYAN}./run_worker.sh series unsolved_mysteries 5${NC} # Chilling historical anomalies"
echo ""
echo -e "  2. Launch continuous overnight infinite multi-niche generator:"
echo -e "     ${GREEN}${BOLD}./run_worker.sh infinite${NC}   (or ${CYAN}cinema-engine infinite${NC})"
echo ""
echo -e "  3. Check active rendering checkpoints & generated videos:"
echo -e "     ${YELLOW}./run_worker.sh status${NC}"
echo ""
echo -e "  4. List all available niche profiles:"
echo -e "     ${MAGENTA}./run_worker.sh profiles${NC}"
echo ""
