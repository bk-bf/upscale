#!/usr/bin/env bash
# provision-box.sh — make a fresh rented GPU box able to run upscale trials.
#
# Runs ON the box. Idempotent: every step checks before it does anything, so a
# re-run after a partial failure costs seconds, not a re-download.
#
# There was no provisioning script for the Bleach run — the box was built by
# hand and the recipe died with it. This is that recipe, written down.
#
# The model is NOT built here. It is converted on the orchestration node (torch
# + pnnx, CPU only) and pushed in, so it survives the box being destroyed.
set -euo pipefail

BENCH=${BENCH:-/root/bench}
RESRGAN_VER=${RESRGAN_VER:-v0.2.0}
RESRGAN_URL="https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan/releases/download/${RESRGAN_VER}/realesrgan-ncnn-vulkan-${RESRGAN_VER}-ubuntu.zip"
FFMPEG_VER=${FFMPEG_VER:-7.0.2}

say(){ printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
die(){ printf 'provision: %s\n' "$*" >&2; exit 1; }

mkdir -p "$BENCH"

# ---------------------------------------------------------------- packages
if ! command -v unzip >/dev/null 2>&1 || ! command -v vulkaninfo >/dev/null 2>&1; then
  say "installing packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq unzip xz-utils bc jq rsync libvulkan1 vulkan-tools libgomp1 >/dev/null
else
  say "packages already present"
fi

# ---------------------------------------------------------------- vulkan
# The GPU must be visible to Vulkan, not just to nvidia-smi. A container with a
# working nvidia-smi and no ICD json fails here, and realesrgan's failure mode
# is a silent fallback to a device that does not exist.
say "vulkan check"
if [ ! -e /usr/share/vulkan/icd.d/nvidia_icd.json ] && [ -e /etc/vulkan/icd.d/nvidia_icd.json ]; then
  mkdir -p /usr/share/vulkan/icd.d
  ln -sf /etc/vulkan/icd.d/nvidia_icd.json /usr/share/vulkan/icd.d/nvidia_icd.json
  say "  linked ICD from /etc into /usr/share"
fi
GPUNAME=$(vulkaninfo --summary 2>/dev/null | grep -m1 "deviceName" | sed 's/.*= *//') || GPUNAME=""
[ -n "$GPUNAME" ] || die "no Vulkan device — realesrgan cannot run here"
say "  vulkan device: $GPUNAME"

# ---------------------------------------------------------------- upscaler
if [ -x "$BENCH/resrgan/realesrgan-ncnn-vulkan" ]; then
  say "realesrgan already installed"
else
  say "downloading realesrgan-ncnn-vulkan $RESRGAN_VER"
  tmp=$(mktemp -d)
  curl -fsSL "$RESRGAN_URL" -o "$tmp/r.zip" || die "realesrgan download failed"
  # The zip carries a top-level directory; flatten it so the binary path is
  # stable across releases.
  unzip -qo "$tmp/r.zip" -d "$tmp/x"
  bin=$(find "$tmp/x" -name realesrgan-ncnn-vulkan -type f | head -1)
  [ -n "$bin" ] || die "no realesrgan binary inside the zip"
  mkdir -p "$BENCH/resrgan"
  cp -f "$bin" "$BENCH/resrgan/realesrgan-ncnn-vulkan"
  chmod +x "$BENCH/resrgan/realesrgan-ncnn-vulkan"
  rm -rf "$tmp"
  say "  installed to $BENCH/resrgan"
fi

# ---------------------------------------------------------------- ffmpeg
# ffmpeg 8.1.2 silently mis-decodes MPEG-4 ASP with a *correct* frame count.
# The box ships 6.1.1; a second, independent decoder is fetched so the
# two-decoder cross-check the pipeline relies on can actually run once here.
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

# ---------------------------------------------------------------- report
say "--- provisioned ---"
printf 'gpu_vulkan\t%s\n'  "$GPUNAME"
printf 'resrgan\t%s\n'     "$([ -x "$BENCH/resrgan/realesrgan-ncnn-vulkan" ] && echo ok || echo MISSING)"
printf 'ffmpeg_sys\t%s\n'  "$(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"
printf 'ffmpeg_static\t%s\n' "$([ -x "$FFSTATIC" ] && "$FFSTATIC" -version 2>/dev/null | head -1 | awk '{print $3}' || echo MISSING)"
printf 'model\t%s\n'       "$([ -f "$BENCH/models_janai/animejanai-suc.param" ] && echo ok || echo 'MISSING — push it from the orchestration node')"
printf 'cpu_quota\t%s\n'   "$(awk '{if($1=="max"){print "unlimited"}else{printf "%.1f", $1/$2}}' /sys/fs/cgroup/cpu.max 2>/dev/null || echo unknown)"
printf 'ram_gb\t%s\n'      "$(free -g | awk '/^Mem:/{print $2}')"
printf 'disk_free_gb\t%s\n' "$(df -BG --output=avail / | tail -1 | tr -dc '0-9')"
