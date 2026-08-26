#!/usr/bin/env bash
set -euo pipefail
PREFIX=${PREFIX:-$HOME/.local}
REPO=$(cd "$(dirname "$0")" && pwd)
LINK=0
[ "${1:-}" = "--link" ] && LINK=1

inst(){
  local src=$1 dst=$2
  mkdir -p "$(dirname "$dst")"
  if [ "$LINK" = 1 ]; then
    ln -sfn "$REPO/$src" "$dst"
  else
    install -m755 "$src" "$dst.new.$$"
    mv -f "$dst.new.$$" "$dst"
  fi
  echo "  $dst"
}

inst upscale                "$PREFIX/bin/upscale"
inst libexec/upscale-worker "$PREFIX/libexec/upscale-worker"
echo "$([ "$LINK" = 1 ] && echo linked || echo installed)."
