#!/usr/bin/env bash
set -euo pipefail

input_dir="${INPUT_DIR:-/input}"
output_dir="${OUTPUT_DIR:-/output}"
scene_class="${SCENE_CLASS:?SCENE_CLASS is required}"
width="${WIDTH:-1920}"
height="${HEIGHT:-1080}"
fps="${FPS:-30}"

mkdir -p "${output_dir}/media"
manim --disable_caching --format mp4 -r "${width},${height}" --fps "${fps}" \
  --media_dir "${output_dir}/media" "${input_dir}/scene.py" "${scene_class}"

rendered="$(find "${output_dir}/media" -type f -name '*.mp4' -print -quit)"
test -n "${rendered}"
cp "${rendered}" "${output_dir}/final.mp4"
ffmpeg -hide_banner -loglevel error -sseof -0.1 -i "${output_dir}/final.mp4" \
  -frames:v 1 "${output_dir}/final.png"
ffmpeg -hide_banner -loglevel error -i "${output_dir}/final.mp4" \
  -vf "fps=1/5,scale=480:-1,tile=4x1" -frames:v 1 "${output_dir}/contact-sheet.png" || \
  cp "${output_dir}/final.png" "${output_dir}/contact-sheet.png"
