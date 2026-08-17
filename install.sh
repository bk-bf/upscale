#!/usr/bin/env bash
# Install upscale + its worker. Only `upscale` lands on $PATH.
#
# The always-on QUEUE DAEMONS are opt-in. `upscale ep <range>` does one job and
# exits, which is what a leftover episode or a short run actually needs, and it
# leaves nothing enabled to wake up on the next boot and start upscaling on its
# own. Install the daemons with:
#
#   WITH_QUEUE_SERVICE=1 ./install.sh
#
# The watch and artwork units are NOT daemons - a timer and two oneshots - so
# they are always installed. Nothing here is enabled by this script either way.
set -euo pipefail
PREFIX="${PREFIX:-$HOME/.local}"
UNITS="$HOME/.config/systemd/user"

install -Dm755 upscale              "$PREFIX/bin/upscale"
install -Dm755 libexec/upscale-worker "$PREFIX/libexec/upscale-worker"
install -Dm755 libexec/upscale-worker-remote "$PREFIX/libexec/upscale-worker-remote"
install -Dm755 libexec/upscale-watch "$PREFIX/bin/upscale-watch"
install -Dm755 libexec/upscale-guard "$PREFIX/bin/upscale-guard"
install -Dm755 libexec/upscale-artwork "$PREFIX/bin/upscale-artwork"

install -Dm644 upscale-watch.service   "$UNITS/upscale-watch.service"
install -Dm644 upscale-watch.timer     "$UNITS/upscale-watch.timer"
install -Dm644 upscale-artwork.service "$UNITS/upscale-artwork.service"
install -Dm644 upscale-artwork.timer   "$UNITS/upscale-artwork.timer"

if [ "${WITH_QUEUE_SERVICE:-0}" = 1 ]; then
  install -Dm644 upscale.service         "$UNITS/upscale.service"
  install -Dm644 upscale-collect.service "$UNITS/upscale-collect.service"
fi
systemctl --user daemon-reload 2>/dev/null || true

echo "installed."
echo
echo "run a range and exit:"
echo "  upscale ep 3-5                       # this library"
echo "  upscale ep 3-5 ubuntu:/mnt/media/tv/Gintama"
echo
if [ "${WITH_QUEUE_SERVICE:-0}" = 1 ]; then
  echo "queue daemons installed (not enabled). to run one at boot:"
  echo "  loginctl enable-linger \"\$USER\""
  echo "  systemctl --user enable --now upscale.service"
else
  echo "queue daemons NOT installed - WITH_QUEUE_SERVICE=1 ./install.sh to add them."
fi
