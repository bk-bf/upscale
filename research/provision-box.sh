#!/usr/bin/env bash
set -euo pipefail

BENCH=${BENCH:-/root/bench}
RESRGAN_VER=${RESRGAN_VER:-v0.2.0}
RESRGAN_URL="https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan/releases/download/${RESRGAN_VER}/realesrgan-ncnn-vulkan-${RESRGAN_VER}-ubuntu.zip"
FFMPEG_VER=${FFMPEG_VER:-7.0.2}

say(){ printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
die(){ printf 'provision: %s\n' "$*" >&2; exit 1; }

mkdir -p "$BENCH"

if ! command -v unzip >/dev/null 2>&1 || ! command -v vulkaninfo >/dev/null 2>&1; then
  say "installing packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq unzip xz-utils bc jq rsync libvulkan1 vulkan-tools libgomp1 >/dev/null
else
  say "packages already present"
fi

say "vulkan check"
if [ ! -e /usr/share/vulkan/icd.d/nvidia_icd.json ] && [ -e /etc/vulkan/icd.d/nvidia_icd.json ]; then
  mkdir -p /usr/share/vulkan/icd.d
  ln -sf /etc/vulkan/icd.d/nvidia_icd.json /usr/share/vulkan/icd.d/nvidia_icd.json
  say "  linked ICD from /etc into /usr/share"
fi
GPUNAME=$(vulkaninfo --summary 2>/dev/null | grep -m1 "deviceName" | sed 's/.*= *//') || GPUNAME=""
[ -n "$GPUNAME" ] || die "no Vulkan device — realesrgan cannot run here"
say "  vulkan device: $GPUNAME"

if [ -x "$BENCH/resrgan/realesrgan-ncnn-vulkan" ]; then
  say "realesrgan already installed"
else
  say "downloading realesrgan-ncnn-vulkan $RESRGAN_VER"
  tmp=$(mktemp -d)
  curl -fsSL "$RESRGAN_URL" -o "$tmp/r.zip" || die "realesrgan download failed"
  unzip -qo "$tmp/r.zip" -d "$tmp/x"
  bin=$(find "$tmp/x" -name realesrgan-ncnn-vulkan -type f | head -1)
  [ -n "$bin" ] || die "no realesrgan binary inside the zip"
  mkdir -p "$BENCH/resrgan"
  cp -f "$bin" "$BENCH/resrgan/realesrgan-ncnn-vulkan"
  chmod +x "$BENCH/resrgan/realesrgan-ncnn-vulkan"
  rm -rf "$tmp"
  say "  installed to $BENCH/resrgan"
fi

FFSTATIC="$BENCH/ffmpeg-${FFMPEG_VER}-amd64-static/ffmpeg"
if [ -x "$FFSTATIC" ]; then
  say "static ffmpeg $FFMPEG_VER already installed"
else
  say "downloading static ffmpeg $FFMPEG_VER"
  tmp=$(mktemp -d)
  ok=0
  for url in \
    "https://johnvansickle.com/ffmpeg/old-releases/ffmpeg-${FFMPEG_VER}-amd64-static.tar.xz" \
    "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"; do
    if curl -fsSL --max-time 300 "$url" -o "$tmp/ff.tar.xz"; then ok=1; break; fi
    say "  miss: $url"
  done
  if [ "$ok" = 1 ]; then
    tar -xf "$tmp/ff.tar.xz" -C "$BENCH"
    d=$(find "$BENCH" -maxdepth 1 -name 'ffmpeg-*-amd64-static' -type d | head -1)
    [ -n "$d" ] && [ "$d" != "$BENCH/ffmpeg-${FFMPEG_VER}-amd64-static" ] && \
      mv -f "$d" "$BENCH/ffmpeg-${FFMPEG_VER}-amd64-static"
    say "  installed: $("$FFSTATIC" -version 2>/dev/null | head -1)"
  else
    say "  WARNING: no static ffmpeg — decoder cross-check will be skipped"
  fi
  rm -rf "$tmp"
fi

say "--- provisioned ---"
printf 'gpu_vulkan\t%s\n'  "$GPUNAME"
printf 'resrgan\t%s\n'     "$([ -x "$BENCH/resrgan/realesrgan-ncnn-vulkan" ] && echo ok || echo MISSING)"
printf 'ffmpeg_sys\t%s\n'  "$(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"
printf 'ffmpeg_static\t%s\n' "$([ -x "$FFSTATIC" ] && "$FFSTATIC" -version 2>/dev/null | head -1 | awk '{print $3}' || echo MISSING)"
printf 'model\t%s\n'       "$([ -f "$BENCH/models_janai/animejanai-suc.param" ] && echo ok || echo 'MISSING — push it from the orchestration node')"
printf 'cpu_quota\t%s\n'   "$(awk '{if($1=="max"){print "unlimited"}else{printf "%.1f", $1/$2}}' /sys/fs/cgroup/cpu.max 2>/dev/null || echo unknown)"
printf 'ram_gb\t%s\n'      "$(free -g | awk '/^Mem:/{print $2}')"
printf 'disk_free_gb\t%s\n' "$(df -BG --output=avail / | tail -1 | tr -dc '0-9')"
