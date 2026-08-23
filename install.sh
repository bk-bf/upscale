#!/usr/bin/env bash
# Install upscale on this machine.
#
#   ./install.sh            -> ~/.local
#   PREFIX=/usr/local ./install.sh
#
# Two files. The driver runs on the machine that holds the media; the worker
# runs on each GPU. Installing both everywhere is harmless and keeps the two
# from drifting apart.
set -euo pipefail
PREFIX=${PREFIX:-$HOME/.local}
inst(){ install -Dm755 "$1" "$2"; echo "  $2"; }
inst upscale                "$PREFIX/bin/upscale"
inst libexec/upscale-worker "$PREFIX/libexec/upscale-worker"
echo "installed."
