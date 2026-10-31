// 시연영상 녹화 공용 도우미: 가짜 커서·자막 오버레이 주입, 부드러운 이동·클릭·타이핑·스크롤.
// 녹화 영상에는 OS 커서가 찍히지 않으므로 페이지 최상위 문서에 커서와 자막을 직접 그린다.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const CAPTIONS = require('./captions.json');

const SITE = 'http://localhost:8000';
const VIEW = { width: 1440, height: 900 };
const OUT = path.join(__dirname, 'out');

const OVERLAY = `(() => {
  if (window !== window.top) return;           // iframe(AI은행원) 안에는 그리지 않는다
  const add = () => {
    if (document.getElementById('__demo_cur')) return;
    const st = document.createElement('style');
    st.textContent = \`
      #__demo_cur{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;pointer-events:none;
        transform:translate(-200px,-200px);transition:transform .7s cubic-bezier(.4,0,.2,1);filter:drop-shadow(0 1px 2px rgba(0,0,0,.35))}
      .__demo_ripple{position:fixed;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;z-index:2147483646;
        pointer-events:none;background:rgba(15,169,104,.35);border:2px solid rgba(15,169,104,.9);animation:__demo_rip .55s ease-out forwards}
      @keyframes __demo_rip{from{transform:scale(.3);opacity:1}to{transform:scale(1.6);opacity:0}}
      #__demo_cap{position:fixed;left:50%;bottom:80px;transform:translate(-50%,8px);max-width:none;padding:14px 26px;border-radius:14px;
        background:rgba(234,241,253,.8);color:#3C4043;border:1px solid #DDE3EA;box-shadow:0 4px 16px rgba(60,64,67,.12);
        -webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);font:600 28px/1.5 "IBM Plex Sans KR",system-ui,sans-serif;
        text-align:center;z-index:2147483645;pointer-events:none;opacity:0;transition:opacity .35s,transform .35s;
        word-break:keep-all;white-space:pre}   /* 자동 줄바꿈 없음: 쉼표·괄호·두 문장일 때만 captions.json 의 \\n 으로 나눈다 */
      #__demo_cap.on{opacity:1;transform:translate(-50%,0)}
      #__demo_title{position:fixed;inset:0;z-index:2147483647;pointer-events:none;display:flex;flex-direction:column;
        align-items:center;justify-content:center;gap:var(--space-5);background:#fff;opacity:0;transition:opacity .6s}
      #__demo_title.on{opacity:1;transition:none}
      #__demo_title.on.fade{transition:opacity .6s}
      #__demo_title img{width:var(--space-9);height:var(--space-9)}
      #__demo_title b{font:400 var(--text-3xl)/1.2 var(--font-display);color:var(--blue)}
      #__demo_title span{font:600 var(--text-2xl)/1.5 var(--font-body);color:var(--text);word-break:keep-all}\`;
    document.documentElement.appendChild(st);
    const c = document.createElement('div');
    c.id = '__demo_cur';
    c.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M4 2l15 11-6.5 1.2L16 21l-3 1.4-3.4-6.8L4 20z" fill="#fff" stroke="#1d1d1f" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(c);
    const cap = document.createElement('div');
    cap.id = '__demo_cap';
    document.documentElement.appendChild(cap);
    const ti = document.createElement('div');   // 영상 첫 화면의 제목 카드(커서보다 나중에 붙여 커서를 가린다)
    ti.id = '__demo_title';
    ti.innerHTML = '<img src="/img/logo-mark.svg" alt=""><b></b><span></span>';
    document.documentElement.appendChild(ti);
    const p = window.__demoLastPos;
    if (p) { c.style.transition = 'none'; c.style.transform = \`translate(\${p[0]}px,\${p[1]}px)\`; }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
  window.__demoMove = (x, y) => { const c = document.getElementById('__demo_cur'); if (!c) return;
    c.style.transition = ''; c.style.transform = \`translate(\${x}px,\${y}px)\`; };
  window.__demoJump = (x, y) => { const c = document.getElementById('__demo_cur'); if (!c) return;
    c.style.transition = 'none'; c.style.transform = \`translate(\${x}px,\${y}px)\`; };
  window.__demoRipple = (x, y) => { const r = document.createElement('div'); r.className = '__demo_ripple';
    r.style.left = x + 'px'; r.style.top = y + 'px'; document.documentElement.appendChild(r); setTimeout(() => r.remove(), 700); };
  // ms 뒤 저절로 사라진다(읽을 시간만 보이고)
  window.__demoCaption = (t, ms) => { const cap = document.getElementById('__demo_cap'); if (!cap) return;
    clearTimeout(window.__demoCapTimer);
    if (!t) { cap.classList.remove('on'); return; } cap.textContent = t; cap.classList.add('on');
    if (ms) window.__demoCapTimer = setTimeout(() => cap.classList.remove('on'), ms); };
  // 편집 표시: 왼쪽 위 8px 자홍색 네모를 잠깐 띄운다. cut.js 가 영상에서 이걸 찾아 자를 위치로 쓴다
  window.__demoMark = (ms) => { const m = document.createElement('div');
    m.style.cssText = 'position:fixed;left:0;top:0;width:8px;height:8px;background:#f0f;z-index:2147483647;pointer-events:none';
    document.documentElement.appendChild(m); setTimeout(() => m.remove(), ms); };
  window.__demoTitle = (main, sub, fade) => { const ti = document.getElementById('__demo_title'); if (!ti) return;
    if (!main) { ti.classList.remove('on', 'fade'); return; }
    ti.querySelector('b').textContent = main; ti.querySelector('span').textContent = sub || '';
    ti.classList.toggle('fade', !!fade); ti.classList.add('on'); };
})();`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Director {
  constructor(page) { this.page = page; this.pos = [VIEW.width - 120, VIEW.height - 160]; this.cap = null; this.capUntil = 0;
    this.startAt = null; this.splices = []; }

  // 영상의 실제 시작점. 앞부분에 작게 찍힌 화면(회색 여백)과 첫 로드·새로고침 깜빡임이 남아서
  // 화면이 자리 잡은 이 시점 앞은 잘라낸다. 녹화 영상의 시간은 실제 경과 시간과 조금씩 어긋나므로
  // (몇 초씩 늘어남) 영상 안에 편집 표시를 찍어 cut.js 가 그 위치를 찾게 한다.
  // 표시를 못 찾을 때를 대비해 '여기부터 녹화 종료까지' 길이도 남긴다(영상 길이에서 빼는 예전 방식).
  async markStart() { this.startAt = Date.now(); log('start'); await this.mark(); }

  // 편집 표시(왼쪽 위 자홍색 네모 0.4초). 표시 구간은 잘려 나가는 쪽에 들어가 완성 영상에는 남지 않는다
  async mark(page = this.page) {
    await page.evaluate(() => window.__demoMark(400));
    await sleep(500);
  }

  // 페이지 이동·새로고침 뒤 오버레이가 다시 붙으면 커서를 복원한다. 자막은 읽을 시간이 남았을 때만
  // 남은 만큼 다시 띄운다(이미 사라진 자막이 화면이 바뀔 때마다 다시 뜨지 않게)
  async restore() {
    await this.page.waitForFunction(() => !!document.getElementById('__demo_cur'), null, { timeout: 10000 }).catch(() => {});
    const left = this.capUntil - Date.now();
    await this.page.evaluate(([x, y, cap, left]) => { window.__demoJump(x, y); if (cap && left > 300) window.__demoCaption(cap, left); },
      [...this.pos, this.cap, left]);
  }

  // 자막은 글자 수에 맞춰 따라 읽을 시간(1.2초 + 글자당 0.11초, 2.5~8초)만 보이고 사라진다
  async caption(n) {
    if (n) log(`caption ${n}`);
    this.cap = n ? CAPTIONS[String(n)] : null;
    const ms = n ? Math.min(8000, Math.max(2500, 1200 + this.cap.replace(/\s/g, '').length * 110)) : 0;
    this.capUntil = Date.now() + ms;
    await this.page.evaluate(([t, ms]) => window.__demoCaption(t, ms), [this.cap, ms]);
    await sleep(n ? 400 : 300);
  }

  // 지금 자막을 다 읽을 때까지 기다린다(다음 자막·화면 전환이 자막을 덮지 않게)
  async waitCaption() {
    const left = this.capUntil - Date.now();
    if (left > 0) await sleep(left + 300);
  }

  // 제목 카드: 자막 n 을 ' - ' 앞(제목)·뒤(부제)로 나눠 화면 가운데에 띄우고 ms 뒤 걷어 낸다.
  // 카드는 바로 켜지므로 markStart 앞에 부르면 영상이 제목으로 시작한다.
  // fade 면 서서히 나타난다(영상 마지막 장면용).
  async title(n, ms = 3000, { fade = false } = {}) {
    log(`title ${n}`);
    const [main, ...rest] = CAPTIONS[String(n)].split(' - ');
    await this.page.evaluate(([m, s, f]) => window.__demoTitle(m, s, f), [main, rest.join(' - '), fade]);
    return async () => { await sleep(ms); await this.page.evaluate(() => window.__demoTitle(null)); await sleep(700); };
  }

  async moveTo(x, y) {
    this.pos = [x, y];
    await this.page.evaluate(([x, y]) => window.__demoMove(x, y), [x, y]);
    await sleep(750);
  }

  // locator 는 page.locator 든 frameLocator(...).locator 든 된다(boundingBox 가 페이지 좌표를 준다)
  async click(locator, { pause = 500 } = {}) {
    await this.reveal(locator);
    await sleep(250);
    const center = async () => { const b = await locator.boundingBox(); return [Math.round(b.x + b.width / 2), Math.round(b.y + b.height / 2)]; };
    let [x, y] = await center();
    await this.moveTo(x, y);
    // 커서가 가는 동안 화면이 밀리면(AI은행원 대화 영역 등) 커서가 버튼 밖에 서 있게 된다 — 다시 재서 맞춘다
    for (let i = 0; i < 3; i++) {
      const [nx, ny] = await center();
      if (Math.abs(nx - x) < 4 && Math.abs(ny - y) < 4) break;
      [x, y] = [nx, ny];
      await this.moveTo(x, y);
    }
    await this.page.evaluate(([x, y]) => window.__demoRipple(x, y), [x, y]);
    await locator.click();
    await sleep(pause);
  }

  // 누르지 않고 커서만 올린다(바꾸면 안 되는 선택지를 짚을 때)
  async hover(locator, { pause = 800 } = {}) {
    await this.reveal(locator);
    const b = await locator.boundingBox();
    await this.moveTo(Math.round(b.x + b.width / 2), Math.round(b.y + b.height / 2));
    await locator.hover();
    await sleep(pause);
  }

  async type(locator, text, { delay = 75 } = {}) {
    await this.click(locator, { pause: 200 });
    await locator.pressSequentially(text, { delay });
    await sleep(400);
  }

  // 요소가 화면 밖(헤더 아래~자막 위 밖)이면 페이지를 천천히 스크롤해 화면 40% 높이에 둔다.
  // AI은행원 iframe 안 요소는 페이지 스크롤로 못 옮기므로 예전처럼 scrollIntoViewIfNeeded.
  async reveal(locator, { speed = 400 } = {}) {
    const top = await locator.evaluate((e) => window === window.top).catch(() => false);
    if (!top) { await locator.scrollIntoViewIfNeeded(); return; }
    const b = await locator.boundingBox();
    if (b && b.y >= 80 && b.y + b.height <= VIEW.height - 180) return;
    const y = await locator.evaluate((e) => e.getBoundingClientRect().top + scrollY);
    await this.scroll(y - VIEW.height * 0.4, { speed, noFooter: true });
    await locator.scrollIntoViewIfNeeded();   // 페이지 안의 스크롤 박스 속 요소면 그 박스만 맞춘다
  }

  // 새 탭으로 열리는 링크: 클릭은 실제로 하되 새 탭 대신 별도 창(컨텍스트)에서 같은 주소를 열어 따로 녹화하고,
  // 천천히 보여 준 뒤 닫는다. build.sh 가 본 영상의 클릭~닫기 구간을 그 영상으로 바꿔 끼운다.
  // 같은 컨텍스트에 새 탭이 생기면 본 탭 녹화 아래쪽이 회색 띠로 찍히는 문제가 있어서(재현 확인) 창을 분리한다.
  async clickPopup(locator, { show = 4500 } = {}) {
    const href = await locator.getAttribute('href');
    const b = await locator.boundingBox();
    const x = Math.round(b.x + b.width / 2), y = Math.round(b.y + b.height / 2);
    await this.moveTo(x, y);
    await this.page.evaluate(([x, y]) => window.__demoRipple(x, y), [x, y]);
    await locator.evaluate((a) => a.addEventListener('click', (e) => e.preventDefault(), { once: true, capture: true }));
    await locator.click();
    await sleep(400);                  // 클릭 효과가 보인 다음부터 바꿔 끼운다
    await this.mark();
    log(`popup ${href}`);
    const dir = path.dirname(await this.page.video().path());
    const ctx = await this.page.context().browser().newContext({ viewport: VIEW, deviceScaleFactor: 1, locale: 'ko-KR',
      recordVideo: { dir, size: VIEW } });
    await ctx.addInitScript(OVERLAY);
    const popup = await ctx.newPage();
    // 외부 사이트가 흰 화면에 머문 적이 있어(35초) 글자가 실제로 보일 때까지 기다리고, 안 되면 다시 연다
    for (let i = 0; i < 3; i++) {
      await (i ? popup.reload({ timeout: 30000 }) : popup.goto(href, { timeout: 30000 })).catch(() => {});
      const ok = await popup.waitForFunction(() => document.body && document.body.innerText.trim().length > 100,
        null, { timeout: 15000 }).then(() => true).catch(() => false);
      log(`popup ${ok ? '표시됨' : '흰 화면 → 다시 열기'}`);
      if (ok) break;
    }
    await popup.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
    await sleep(700);
    await popup.evaluate(([x, y]) => window.__demoJump && window.__demoJump(x, y), this.pos).catch(() => {});
    await sleep(800);
    const tShow = Date.now();
    await withTimeout(popup.evaluate(async (dur) => {
      const max = Math.min(600, document.scrollingElement.scrollHeight - innerHeight);
      await new Promise((res) => { const t0 = Date.now(); const tick = () => { const k = Math.min(1, (Date.now() - t0) / dur);
        scrollTo(0, max * (k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2)); k < 1 ? setTimeout(tick, 16) : res(); }; tick(); });
    }, 2500), 8000, 'popup scroll');
    await sleep(Math.max(0, show - (Date.now() - tShow)));
    const file = await popup.video().path();
    await ctx.close();
    this.splices.push({ popup: file, popupTail: (Date.now() - tShow) / 1000 });
    await this.mark();                 // 여기서부터 본 영상으로 돌아온다
  }

  // 문서를 부드럽게 스크롤. to: 'bottom'(푸터 포함) | 'footer'(푸터 직전) | 'top' | 숫자(px)
  // requestAnimationFrame 은 헤드리스에서 멈출 때가 있어(2시간 정지 사고) 타이머로 움직이고,
  // 페이지 쪽 대기는 최대 20초로 끊는다(page.evaluate 에는 자체 타임아웃이 없다).
  // noFooter: 어디로 가든 푸터가 화면에 들어오기 직전까지만 내려간다
  async scroll(to, { speed = 700, noFooter = false } = {}) {
    const job = this.page.evaluate(async ([to, speed, noFooter]) => {
      const se = document.scrollingElement;
      const max = se.scrollHeight - innerHeight;
      let target = to;
      if (to === 'top') target = 0;
      if (to === 'bottom') target = max;
      if (to === 'footer') {
        const f = document.querySelector('.site-footer');
        target = f ? f.getBoundingClientRect().top + scrollY - innerHeight : max;
      }
      target = Math.max(0, Math.min(max, Number(target) || 0));
      if (noFooter) {
        const f = document.querySelector('.site-footer');
        if (f) target = Math.max(0, Math.min(target, f.getBoundingClientRect().top + scrollY - innerHeight));
      }
      const from = scrollY, dist = target - from;
      const dur = Math.min(15000, Math.max(400, Math.abs(dist) / speed * 1000));
      await new Promise((res) => {
        const t0 = Date.now();
        const tick = () => {
          const k = Math.min(1, (Date.now() - t0) / dur);
          const e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
          scrollTo(0, from + dist * e);
          k < 1 ? setTimeout(tick, 16) : res();
        };
        tick();
      });
    }, [to, speed, noFooter]);
    await withTimeout(job, 20000, 'scroll');
    await sleep(500);
  }
}

// page.evaluate 같은 무기한 대기를 끊는다. 끊겨도 녹화는 계속 진행한다(로그만 남김).
async function withTimeout(p, ms, what) {
  let timer;
  const t = new Promise((res) => { timer = setTimeout(() => { log(`TIMEOUT ${what} ${ms}ms`); res(); }, ms); });
  try { return await Promise.race([p.catch((e) => log(`ERR ${what} ${e.message.split('\n')[0]}`)), t]); }
  finally { clearTimeout(timer); }
}

const T0 = Date.now();
function log(msg) { console.error(`[${((Date.now() - T0) / 1000).toFixed(1)}s] ${msg}`); }

async function openRecorder(name) {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({
    viewport: VIEW, deviceScaleFactor: 1, locale: 'ko-KR',
    recordVideo: { dir: path.join(OUT, name), size: VIEW },
  });
  await context.addInitScript(OVERLAY);
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  return { browser, context, page, d: new Director(page) };
}

async function closeRecorder({ browser, context, page, d }) {
  const video = page.video();
  const tail = d.startAt ? (Date.now() - d.startAt) / 1000 : null;
  await context.close();
  await browser.close();
  if (!video) return null;
  const file = await video.path();
  if (tail) fs.writeFileSync(file + '.tail', tail.toFixed(2));   // build.sh 가 읽는 '시작점~끝' 길이
  // 새 탭 영상 끼워 넣기(cut.js): 본 영상의 편집 표시 두 개(클릭·복귀) 사이를 새 탭 영상의 끝 popupTail 초로 바꾼다
  if (d.splices.length) fs.writeFileSync(file + '.splice.json', JSON.stringify(d.splices));
  return file;
}

module.exports = { SITE, VIEW, OUT, sleep, log, withTimeout, openRecorder, closeRecorder };
