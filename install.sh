#!/usr/bin/env bash
# upscale-ui installer.
#
#   ./install.sh                 build the SPA (nothing else)
#   ./install.sh --with-units    also write the systemd --user unit, not enabled
#   ./install.sh --uninstall     remove the unit
#
# The backend is stdlib Python run in place, so there is nothing to install for
# it - the unit points at this checkout. Building the SPA needs pnpm; if it is
# missing the server still runs and serves a "UI not built" message rather than
# a stack trace.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [ "${1:-}" = --uninstall ]; then
  systemctl --user disable --now upscale-ui.service 2>/dev/null || true
  rm -f "$UNIT_DIR/upscale-ui.service"
  systemctl --user daemon-reload 2>/dev/null || true
  echo "removed upscale-ui.service"; exit 0
fi

[ -f "$SRC/config.json" ] || { cp "$SRC/config.example.json" "$SRC/config.json"; echo "wrote config.json"; }

if command -v pnpm >/dev/null 2>&1; then
  ( cd "$SRC/web" && pnpm install --silent && pnpm build >/dev/null ) && echo "built web/dist"
else
  echo "pnpm not found - skipping the SPA build (the API still works)" >&2
fi

if [ "${1:-}" = --with-units ]; then
  mkdir -p "$UNIT_DIR"
  sed "s|@SRC@|$SRC|g" "$SRC/deploy/upscale-ui.service" > "$UNIT_DIR/upscale-ui.service.new.$$"
  mv -f "$UNIT_DIR/upscale-ui.service.new.$$" "$UNIT_DIR/upscale-ui.service"
  systemctl --user daemon-reload 2>/dev/null || true
  echo "wrote $UNIT_DIR/upscale-ui.service (not enabled)"
  echo "  loginctl enable-linger \"\$USER\""
  echo "  systemctl --user enable --now upscale-ui"
fi
