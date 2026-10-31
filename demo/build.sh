#!/bin/sh
# 녹화된 세그먼트(.webm)를 mp4 로 바꾸고 A→B 순서로 이어 붙인다(B 에 백오피스까지 들어 있다).
# 사용법: ./build.sh [출력 경로]   (기본: ~/Desktop/매치뱅크_시연.mp4)
set -e
cd "$(dirname "$0")"
FF=node_modules/ffmpeg-static/ffmpeg
OUT_MP4="${1:-$HOME/Desktop/매치뱅크_시연.mp4}"
: > out/list.txt
for s in A B; do
  # 세그먼트별 가장 최근 녹화. 같은 폴더의 새 탭 영상(.tail 없음)은 제외한다
  V=$(ls -t out/seg$s/*.webm.tail | head -1); V=${V%.tail}
  # 편집 표시를 못 찾을 때 쓸 시작점: 영상 길이 - (markStart~녹화 종료). 기록이 없으면 자르지 않는다
  DUR=$($FF -i "$V" 2>&1 | sed -n 's/.*Duration: \([0-9:.]*\).*/\1/p' | awk -F: '{print $1*3600+$2*60+$3}')
  SS=$(awk -v d="$DUR" -v t="$(cat "$V.tail" 2>/dev/null || echo "$DUR")" 'BEGIN{s=d-t; print (s>0?s:0)}')
  node cut.js "$V" "$SS" out/seg$s.mp4           # 편집 표시로 앞 자르기 + 새 탭(은행 사이트) 영상 끼워 넣기
  echo "file '$PWD/out/seg$s.mp4'" >> out/list.txt
done
# 임시 파일로 만든 뒤 바꿔 넣는다 — 같은 파일을 덮어쓰면 그 파일을 열어 둔 QuickTime 이 첫 화면에서 멈춘다
$FF -y -loglevel error -f concat -safe 0 -i out/list.txt -c:v libx264 -preset medium -crf 20 \
  -pix_fmt yuv420p -movflags +faststart out/final.tmp.mp4
mv -f out/final.tmp.mp4 "$OUT_MP4"
$FF -i "$OUT_MP4" 2>&1 | grep Duration
echo "저장: $OUT_MP4"
