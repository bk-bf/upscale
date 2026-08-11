#!/usr/bin/env bash
# Install upscale + its worker. Only `upscale` lands on $PATH.
set -euo pipefail
PREFIX="${PREFIX:-$HOME/.local}"
install -Dm755 upscale              "$PREFIX/bin/upscale"
install -Dm755 libexec/upscale-worker "$PREFIX/libexec/upscale-worker"
install -Dm755 libexec/upscale-worker-remote "$PREFIX/libexec/upscale-worker-remote"
install -Dm755 libexec/upscale-watch "$PREFIX/bin/upscale-watch"
install -Dm755 libexec/upscale-guard "$PREFIX/bin/upscale-guard"
install -Dm755 libexec/upscale-artwork "$PREFIX/bin/upscale-artwork"
install -Dm644 upscale.service      "$HOME/.config/systemd/user/upscale.service"
install -Dm644 upscale-collect.service "$HOME/.config/systemd/user/upscale-collect.service"
install -Dm644 upscale-watch.service   "$HOME/.config/systemd/user/upscale-watch.service"
install -Dm644 upscale-watch.timer     "$HOME/.config/systemd/user/upscale-watch.timer"
install -Dm644 upscale-artwork.service "$HOME/.config/systemd/user/upscale-artwork.service"
install -Dm644 upscale-artwork.timer   "$HOME/.config/systemd/user/upscale-artwork.timer"
systemctl --user daemon-reload 2>/dev/null || true
echo "installed. enable at boot with:"
echo "  loginctl enable-linger \"\$USER\""
echo "  systemctl --user enable --now upscale.service"
