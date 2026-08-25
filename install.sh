#!/usr/bin/env bash
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
