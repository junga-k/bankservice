#!/bin/sh
# 녹화된 세그먼트(.webm)를 mp4 로 바꾸고 A→B→C 순서로 이어 붙인다.
# 사용법: ./build.sh [출력 경로]   (기본: ~/Desktop/매치뱅크_시연.mp4)
set -e
cd "$(dirname "$0")"
FF=node_modules/ffmpeg-static/ffmpeg
OUT_MP4="${1:-$HOME/Desktop/매치뱅크_시연.mp4}"
: > out/list.txt
for s in A B C; do
  V=$(ls -t out/seg$s/*.webm | head -1)          # 세그먼트별 가장 최근 녹화
  $FF -y -loglevel error -i "$V" -c:v libx264 -preset veryfast -pix_fmt yuv420p out/seg$s.mp4
  echo "file '$PWD/out/seg$s.mp4'" >> out/list.txt
done
$FF -y -loglevel error -f concat -safe 0 -i out/list.txt -c:v libx264 -preset medium -crf 20 \
  -pix_fmt yuv420p -movflags +faststart "$OUT_MP4"
$FF -i "$OUT_MP4" 2>&1 | grep Duration
echo "저장: $OUT_MP4"
