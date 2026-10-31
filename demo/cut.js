// 세그먼트 webm → mp4. 녹화 영상의 시간은 실제 경과 시간과 조금씩 어긋나서(몇 초씩 늘어남) 시각 계산으로는
// 자를 위치가 틀린다. 그래서 lib.js 가 영상 안에 찍은 편집 표시(왼쪽 위 자홍색 8px 네모)를 찾아 자른다.
//  - 첫 표시(markStart): 이 표시가 끝난 지점부터 쓴다(앞의 회색 여백·로드 깜빡임 제거)
//  - 그다음 표시 두 개씩(clickPopup 의 클릭·복귀): 그 사이를 새 탭(은행 사이트) 영상의 끝 popupTail 초로 바꾼다
// 표시를 못 찾으면 build.sh 가 넘긴 SS(영상 길이 - 시작점~끝 길이)부터 그대로 변환한다.
// 사용법: node cut.js <세그먼트.webm> <SS> <출력.mp4>
const { execFileSync, spawnSync } = require('child_process');
const fs = require('fs');
const FF = require('ffmpeg-static');
const [main, ss, out] = process.argv.slice(2);
const FPS = 25;

const dur = (f) => {
  const txt = String(spawnSync(FF, ['-i', f]).stderr);
  const [h, m, s] = txt.match(/Duration: ([0-9:.]+)/)[1].split(':').map(Number);
  return h * 3600 + m * 60 + s;
};

// 왼쪽 위 4×4 픽셀 평균이 자홍색인 프레임 구간들 → [[시작초, 끝초], ...]
function marks(f) {
  const buf = execFileSync(FF, ['-loglevel', 'error', '-i', f, '-vf', `fps=${FPS},crop=4:4:2:2`,
    '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], { maxBuffer: 1 << 28 });
  const runs = [];
  let on = -1;
  const n = buf.length / 48;
  for (let i = 0; i <= n; i++) {
    let hit = false;
    if (i < n) {
      let r = 0, g = 0, b = 0;
      for (let k = 0; k < 16; k++) { r += buf[i * 48 + k * 3]; g += buf[i * 48 + k * 3 + 1]; b += buf[i * 48 + k * 3 + 2]; }
      hit = r / 16 > 180 && g / 16 < 90 && b / 16 > 180;
    }
    if (hit && on < 0) on = i;
    if (!hit && on >= 0) { runs.push([on / FPS, i / FPS]); on = -1; }
  }
  return runs;
}

const splices = fs.existsSync(main + '.splice.json') ? JSON.parse(fs.readFileSync(main + '.splice.json', 'utf8')) : [];
const runs = marks(main);
const enc = ['-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', out];
if (runs.length < 1 + splices.length * 2) {
  if (splices.length) throw new Error(`편집 표시 부족: ${runs.length}개 (필요 ${1 + splices.length * 2})`);
  console.error(`cut: 편집 표시 없음 → SS ${ss}s 부터`);
  execFileSync(FF, ['-y', '-loglevel', 'error', '-ss', ss, '-i', main, ...enc], { stdio: 'inherit' });
  process.exit(0);
}
const inputs = ['-i', main];
const parts = [];          // [입력 번호, 시작, 끝(null=끝까지)]
let at = runs[0][1];
splices.forEach((sp, i) => {
  const [click, back] = [runs[1 + i * 2], runs[2 + i * 2]];
  parts.push([0, at, click[0]]);
  inputs.push('-i', sp.popup);
  parts.push([i + 1, Math.max(0, dur(sp.popup) - sp.popupTail), null]);
  at = back[1];
});
parts.push([0, at, null]);
const nMain = parts.filter((p) => p[0] === 0).length;
let fc = `[0:v]fps=${FPS},split=${nMain}${Array.from({ length: nMain }, (_, k) => `[m${k}]`).join('')};`;
let k = 0;
parts.forEach(([inp, a, b], n) => {
  const src = inp === 0 ? `[m${k++}]` : `[${inp}:v]fps=${FPS},`;
  fc += `${src}trim=start=${a.toFixed(3)}${b != null ? `:end=${b.toFixed(3)}` : ''},setpts=PTS-STARTPTS[p${n}];`;
});
fc += parts.map((_, n) => `[p${n}]`).join('') + `concat=n=${parts.length}:v=1:a=0[v]`;
execFileSync(FF, ['-y', '-loglevel', 'error', ...inputs, '-filter_complex', fc, '-map', '[v]', ...enc], { stdio: 'inherit' });
console.error(`cut: 시작 ${runs[0][1]}s, 새 탭 ${splices.length}개 끼워 넣음 → ${out}`);
