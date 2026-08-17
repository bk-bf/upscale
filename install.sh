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

# ATOMIC installs. `install` truncates the destination in place, keeping the
# inode - and bash reads a script incrementally, holding a file offset. So
# installing over a RUNNING worker can make it read the new file's bytes at the
# old offset and execute garbage, mid-episode, hours in. Writing beside the
# target and renaming gives the running process its original inode untouched.
inst(){ # $1 = source  $2 = destination
  mkdir -p "$(dirname "$2")"
  cp "$1" "$2.new.$$"
  chmod "${MODE:-755}" "$2.new.$$"
  mv -f "$2.new.$$" "$2"
}

inst upscale                            "$PREFIX/bin/upscale"
inst libexec/upscale-worker             "$PREFIX/libexec/upscale-worker"
inst libexec/upscale-worker-remote      "$PREFIX/libexec/upscale-worker-remote"
inst libexec/upscale-watch              "$PREFIX/bin/upscale-watch"
inst libexec/upscale-guard              "$PREFIX/bin/upscale-guard"
inst libexec/upscale-artwork            "$PREFIX/bin/upscale-artwork"

install -Dm644 upscale-watch.service   "$UNITS/upscale-watch.service"
install -Dm644 upscale-watch.timer     "$UNITS/upscale-watch.timer"
install -Dm644 upscale-artwork.service "$UNITS/upscale-artwork.service"
install -Dm644 upscale-artwork.timer   "$UNITS/upscale-artwork.timer"

if [ "${WITH_QUEUE_SERVICE:-0}" = 1 ]; then
  MODE=644 inst upscale.service         "$UNITS/upscale.service"
  MODE=644 inst upscale-collect.service "$UNITS/upscale-collect.service"
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
