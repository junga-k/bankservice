#!/bin/sh
# 녹화 도구 설치(이 폴더 안에만 설치된다). 브라우저는 맥에 설치된 Chrome 을 쓴다.
set -e
cd "$(dirname "$0")"
npm install
# Playwright 는 영상 저장에 자체 ffmpeg 를 찾는다. 공식 다운로드(npx playwright install ffmpeg)가
# 중간에 멈춰 깨진 파일을 남긴 적이 있어서(2026-10-27), ffmpeg-static 바이너리를 그 자리에 둔다.
# Playwright 1.49.1 기준 경로가 ffmpeg-1010 이다 — 버전을 올리면 에러 메시지의 경로로 바꿀 것.
mkdir -p pw-browsers/ffmpeg-1010
cp node_modules/ffmpeg-static/ffmpeg pw-browsers/ffmpeg-1010/ffmpeg-mac
echo "준비 완료"
