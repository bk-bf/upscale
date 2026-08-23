#!/usr/bin/env bash
# Install upscale on this machine.
#
#   ./install.sh            -> ~/.local
#   PREFIX=/usr/local ./install.sh
#
# Two files. The driver runs on the machine that holds the media; the worker
# runs on each GPU. Installing both everywhere is harmless and keeps them from
# drifting apart.
#
# The install is ATOMIC - write beside the target, then rename. `install` and
# `cp` truncate in place and keep the inode, and bash reads a script
# incrementally: overwriting a running upscale makes it read the new file's
# bytes at its old offset and execute garbage, mid-episode, hours in. A rename
# leaves the running process on the old inode until it exits.
set -euo pipefail
PREFIX=${PREFIX:-$HOME/.local}
inst(){
  local src=$1 dst=$2
  mkdir -p "$(dirname "$dst")"
  install -m755 "$src" "$dst.new.$$"
  mv -f "$dst.new.$$" "$dst"
  echo "  $dst"
}
inst upscale                "$PREFIX/bin/upscale"
inst libexec/upscale-worker "$PREFIX/libexec/upscale-worker"
echo "installed."
