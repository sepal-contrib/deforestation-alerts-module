#!/usr/bin/env bash
# Render the docs animations to doc/anim/<name>.gif.
#
#   tools/docs/anim/build.sh [name ...]     (default: all five)
#
# Each <name>/ is a HyperFrames composition (960x540). It is rendered to MP4,
# then converted to a looping 720 px, 12 fps GIF with a generated palette.
# Needs node (npx), ffmpeg and ffprobe on PATH (e.g. the defo-alerts micromamba
# env's bin), and network access (npm for hyperframes, jsDelivr for GSAP). The GIFs must stay under 2 MB (check_docs).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="$(cd "$here/../../../doc/anim" && pwd)"
names=("$@")
[ ${#names[@]} -eq 0 ] && names=(workflow integrate lookback growth evaluators)
for n in "${names[@]}"; do
  mp4="$here/$n/renders/$n.mp4"
  (cd "$here/$n" && npx --yes hyperframes@0.8.177 render --quiet --fps 24 -o "renders/$n.mp4")
  ffmpeg -y -loglevel error -i "$mp4" -vf \
    "fps=12,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
    -loop 0 "$out/$n.gif"
  printf '%s %s bytes\n' "$out/$n.gif" "$(stat -c %s "$out/$n.gif")"
done
