#!/usr/bin/env bash
# Install upscale + its worker. Only `upscale` lands on $PATH.
set -euo pipefail
PREFIX="${PREFIX:-$HOME/.local}"
install -Dm755 upscale              "$PREFIX/bin/upscale"
install -Dm755 libexec/upscale-worker "$PREFIX/libexec/upscale-worker"
install -Dm644 upscale.service      "$HOME/.config/systemd/user/upscale.service"
systemctl --user daemon-reload 2>/dev/null || true
echo "installed. enable at boot with:"
echo "  loginctl enable-linger \"\$USER\""
echo "  systemctl --user enable --now upscale.service"
