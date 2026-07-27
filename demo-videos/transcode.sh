#!/usr/bin/env bash
# transcode.sh — turn raw auto-loom recordings into web-ready, silent,
# 1280w H.264 clips in frontend/public/videos/, plus refreshed JPEG posters
# taken from each clip's first frame.
#
# Usage:
#   bash transcode.sh               # all recorded slugs
#   bash transcode.sh wargame       # one slug

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
DEST="$REPO/frontend/public/videos"
export PATH="$HOME/.local/ffmpeg/bin:$PATH"

mkdir -p "$DEST"

SLUGS=("$@")
if [ ${#SLUGS[@]} -eq 0 ]; then
  SLUGS=(ask-anything risk-feed knowledge-graph monitoring coa wargame)
fi

for slug in "${SLUGS[@]}"; do
  src="$HERE/demo-video-out/$slug/$slug-demo.mp4"
  if [ ! -f "$src" ]; then
    echo "skip $slug — no recording at $src"
    continue
  fi
  echo "transcoding $slug..."
  # The raw recording starts with page-load lead-in before beat pacing kicks
  # in; keep only the storyboard's worth of footage from the end (beats total
  # + the renderer's ~1.2s trailing hold).
  keep=$(python3 -c "
import json, subprocess, sys
durs = json.load(open('$HERE/demo-video-out/$slug/audio/durations.json'))
probe = subprocess.run(['ffprobe','-v','quiet','-print_format','json','-show_format','$src'], capture_output=True, text=True)
src_dur = float(json.loads(probe.stdout)['format']['duration'])
keep = sum(durs) + 1.2
print(f'{max(0.0, src_dur - keep):.2f}')
")
  ffmpeg -y -ss "$keep" -i "$src" \
    -an \
    -vf "scale=1280:-2" \
    -c:v libx264 -preset slow -crf 26 -pix_fmt yuv420p \
    -movflags +faststart \
    "$DEST/$slug.mp4" 2>/dev/null
  # Poster: a mid-video frame (first frames can be mid-load).
  ffmpeg -y -ss 3 -i "$DEST/$slug.mp4" -frames:v 1 -q:v 4 "$DEST/$slug.jpg" 2>/dev/null
  ls -la "$DEST/$slug.mp4" "$DEST/$slug.jpg" | awk '{print "  " $5 "\t" $9}'
done

echo "done — clips in $DEST"
