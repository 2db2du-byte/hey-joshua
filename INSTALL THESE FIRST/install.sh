#!/bin/bash
# ============================================================================
#  Joshua installer — brings Joshua to life on this machine.
#  Works with the joshua folder anywhere (home folder, external drive, ...).
#  Safe to run again at any time: it only fixes what's missing.
#
#    bash "INSTALL THESE FIRST/install.sh"
# ============================================================================
set -u
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
J="$(dirname "$HERE")"
PKGS="python3 python3-venv python3-dev build-essential kiwix-tools aria2 pipewire-bin curl ca-certificates"
B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; X=$'\e[0m'
step(){ echo; echo "${B}== $*${X}"; }
ok(){ echo "  ${G}✓${X} $*"; }
warn(){ echo "  ${Y}!${X} $*"; }
die(){ echo "  ${R}✗ $*${X}"; exit 1; }

echo "${B}Joshua installer${X} — folder: $J"
[ "$(uname -m)" = "x86_64" ] || die "Joshua's brain and voice are built for 64-bit Intel/AMD PCs (this is $(uname -m))."
[ -f "$J/joshua.py" ] || die "Can't find joshua.py next to this folder. Keep 'INSTALL THESE FIRST' inside the joshua folder."

# Install packages from Joshua's own 'debs' folder (no internet), falling back to the internet.
TMP=""
offline_install() {
  if [ -z "$TMP" ]; then
    TMP=$(mktemp -d /tmp/joshua-debs.XXXX); cp "$HERE"/debs/* "$TMP"/; chmod -R a+rX "$TMP"
    echo "deb [trusted=yes] file:$TMP ./" > "$TMP/local.list"
    trap 'rm -rf "$TMP"' EXIT
  fi
  local APT=(-o Dir::Etc::sourcelist="$TMP/local.list" -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0)
  sudo apt-get "${APT[@]}" update -qq && sudo apt-get "${APT[@]}" install -y --no-install-recommends "$@" && return 0
  warn "offline packages didn't fit this Ubuntu version — trying the internet instead"
  sudo apt-get update && sudo apt-get install -y "$@"
}
command -v apt-get >/dev/null || die "This isn't an Ubuntu/Debian system. Joshua's installer needs Ubuntu (or Xubuntu, Kubuntu, Mint, Pop!_OS...)."
echo "  You may be asked for your password (it's for installing system programs)."

# ---------------------------------------------------------------------------
step "1/7  System programs (Python, library server, audio, compiler)"
missing=""
for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
if [ -z "$missing" ]; then
  ok "all installed"
else
  echo "  missing:$missing"
  offline_install $missing && ok "installed" || die "couldn't install:$missing"
fi

# ---------------------------------------------------------------------------
step "2/7  Graphics card"
GPU_LINE=$(lspci 2>/dev/null | grep -iE '(VGA|3D).*NVIDIA' | head -1)
if [ -z "$GPU_LINE" ]; then
  ok "no NVIDIA card — Joshua will use his smaller brain on the CPU"
elif nvidia-smi >/dev/null 2>&1; then
  ok "NVIDIA driver working: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1)"
else
  echo "  found: ${GPU_LINE#*: }"
  # GTX 16 / RTX 20 and newer (chips TU, GA, AD, GB, GH) use NVIDIA's open driver, which Joshua carries.
  if echo "$GPU_LINE" | grep -qE '\b(TU|GA|AD|GB|GH)[0-9]{3}'; then
    echo "  Installing NVIDIA driver 595 (open) + matching Linux kernel from Joshua's packages..."
    if offline_install linux-image-7.0.0-43-generic linux-modules-7.0.0-43-generic linux-modules-nvidia-595-open-7.0.0-43-generic nvidia-driver-595-open; then
      ok "driver installed"
      echo
      echo "  ${Y}${B}REBOOT NOW${X}${Y}, then run this installer again to finish.${X}"
      exit 0
    else
      warn "driver install failed — Joshua will still work, just on the CPU (slow)"
    fi
  else
    warn "this is an older NVIDIA card (GTX 10-series or earlier). It needs NVIDIA's older driver, which is on the Ubuntu USB:"
    warn "reinstall Ubuntu and tick 'Install third-party software for graphics', or with internet: sudo ubuntu-drivers install"
    warn "Joshua will work on the CPU in the meantime."
  fi
fi

# ---------------------------------------------------------------------------
step "3/7  Joshua's Python (ears + voice)"
if "$J/.venv/bin/python" -c "import faster_whisper, piper, numpy" 2>/dev/null; then
  ok "working"
else
  warn "his Python parts don't match this machine's Python — rebuilding (needs internet, ~1 GB)"
  python3 -m venv --clear "$J/.venv" && "$J/.venv/bin/pip" install -q --upgrade pip \
    && "$J/.venv/bin/pip" install -q -r "$HERE/requirements-joshua.txt" && ok "rebuilt" \
    || warn "rebuild failed — connect to the internet and run this installer again"
fi

step "4/7  Image engine Python (ComfyUI)"
if "$J/ComfyUI/.venv/bin/python" -c "import torch" 2>/dev/null; then
  ok "working"
else
  warn "rebuilding ComfyUI's Python (needs internet, ~5 GB)"
  python3 -m venv --clear "$J/ComfyUI/.venv" && "$J/ComfyUI/.venv/bin/pip" install -q --upgrade pip \
    && "$J/ComfyUI/.venv/bin/pip" install -q --extra-index-url https://download.pytorch.org/whl/cu130 -r "$HERE/requirements-comfyui.txt" \
    && ok "rebuilt" || warn "rebuild failed — images won't work until this succeeds (everything else will)"
fi

# ---------------------------------------------------------------------------
step "5/7  Bookshelf index"
cd "$J/bookshelf" || die "bookshelf folder missing"
rm -f library.xml; n=0
for z in zim/*.zim; do [ -f "$z" ] && kiwix-manage library.xml add "$z" && n=$((n+1)); done
ok "$n books shelved"

# ---------------------------------------------------------------------------
step "6/7  On-switches (background services) and commands"
SD="$HOME/.config/systemd/user"; mkdir -p "$SD" "$HOME/.local/bin"
for f in "$HERE"/services/*.service; do
  sed "s|@JOSHUA_DIR@|$J|g" "$f" > "$SD/$(basename "$f")"
done
systemctl --user daemon-reload
systemctl --user disable --now joshua-studio >/dev/null 2>&1; rm -f "$SD/joshua-studio.service"
systemctl --user enable --now joshua-brain joshua-library joshua-hud >/dev/null 2>&1 && ok "brain, bookshelf and HUD start automatically at login"
systemctl --user restart joshua-brain joshua-library joshua-hud
ln -sf "$J/start-joshua.sh" "$HOME/.local/bin/joshua"
ln -sf "$J/start-hud.sh" "$HOME/.local/bin/joshua-hud"
ln -sf "$J/start-hud.sh" "$HOME/.local/bin/joshua-studio"
chmod +x "$J"/*.sh "$J/ollama/bin/ollama" 2>/dev/null
ok "commands: joshua (voice in terminal), joshua-hud (opens the HUD)"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "log out and back in once so the 'joshua' command is found";; esac

# ---------------------------------------------------------------------------
step "7/7  Health check"
python3 -I "$J/hardware.py" | while IFS= read -r l; do case "$l" in " -"*) warn "${l# - }";; *) ok "Joshua will run in $l";; esac; done
for i in $(seq 30); do curl -s localhost:11435/api/tags >/dev/null && break; sleep 1; done
curl -s localhost:11435/api/tags | grep -q qwen3.5 && ok "brain online" || warn "brain not answering yet: systemctl --user status joshua-brain"
for i in $(seq 15); do curl -s localhost:8090/ >/dev/null && break; sleep 1; done
curl -s localhost:8090/ >/dev/null && ok "bookshelf online (http://127.0.0.1:8090)" || warn "bookshelf not answering: systemctl --user status joshua-library"
command -v pw-record >/dev/null && ok "audio system found" || warn "no PipeWire audio — Joshua can still be used with: joshua --text"

echo
echo "${G}${B}Done.${X} Open a terminal and type:  ${B}joshua${X}   (or  joshua --text  to type instead of talk)"
echo "Joshua's HUD in the browser:  ${B}joshua-hud${X}   (or open http://127.0.0.1:8899)"
