// 시연영상 녹화 공용 도우미: 가짜 커서·자막 오버레이 주입, 부드러운 이동·클릭·타이핑·스크롤.
// 녹화 영상에는 OS 커서가 찍히지 않으므로 페이지 최상위 문서에 커서와 자막을 직접 그린다.
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
      #__demo_cap{position:fixed;left:50%;bottom:36px;transform:translate(-50%,8px);max-width:78%;padding:14px 26px;border-radius:14px;
        background:rgba(28,32,36,.86);color:#fff;font:600 22px/1.5 "IBM Plex Sans KR",system-ui,sans-serif;text-align:center;
        z-index:2147483645;pointer-events:none;opacity:0;transition:opacity .35s,transform .35s;word-break:keep-all}
      #__demo_cap.on{opacity:1;transform:translate(-50%,0)}
      #__demo_cap.top{bottom:auto;top:78px}\`;
    document.documentElement.appendChild(st);
    const c = document.createElement('div');
    c.id = '__demo_cur';
    c.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M4 2l15 11-6.5 1.2L16 21l-3 1.4-3.4-6.8L4 20z" fill="#fff" stroke="#1d1d1f" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(c);
    const cap = document.createElement('div');
    cap.id = '__demo_cap';
    document.documentElement.appendChild(cap);
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
  window.__demoCaption = (t, top) => { const cap = document.getElementById('__demo_cap'); if (!cap) return;
    cap.classList.toggle('top', !!top);
    if (!t) { cap.classList.remove('on'); return; } cap.textContent = t; cap.classList.add('on'); };
})();`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Director {
  constructor(page) { this.page = page; this.pos = [VIEW.width - 120, VIEW.height - 160]; this.cap = null; this.top = false; }

  // 자막 위치: AI은행원처럼 화면 아래에 입력창이 있는 화면은 위쪽에 띄운다
  setCaptionTop(top) { this.top = top; }

  // 페이지 이동·새로고침 뒤 오버레이가 다시 붙으면 커서·자막 상태를 복원한다
  async restore() {
    await this.page.waitForFunction(() => !!document.getElementById('__demo_cur'), null, { timeout: 10000 }).catch(() => {});
    await this.page.evaluate(([x, y, cap, top]) => { window.__demoJump(x, y); if (cap) window.__demoCaption(cap, top); }, [...this.pos, this.cap, this.top]);
  }

  async caption(n) {
    if (n) log(`caption ${n}`);
    this.cap = n ? CAPTIONS[String(n)] : null;
    await this.page.evaluate(([t, top]) => window.__demoCaption(t, top), [this.cap, this.top]);
    await sleep(n ? 400 : 300);
  }

  async moveTo(x, y) {
    this.pos = [x, y];
    await this.page.evaluate(([x, y]) => window.__demoMove(x, y), [x, y]);
    await sleep(750);
  }

  // locator 는 page.locator 든 frameLocator(...).locator 든 된다(boundingBox 가 페이지 좌표를 준다)
  async click(locator, { pause = 500 } = {}) {
    await locator.scrollIntoViewIfNeeded();
    await sleep(250);
    const b = await locator.boundingBox();
    const x = Math.round(b.x + b.width / 2), y = Math.round(b.y + b.height / 2);
    await this.moveTo(x, y);
    await this.page.evaluate(([x, y]) => window.__demoRipple(x, y), [x, y]);
    await locator.click();
    await sleep(pause);
  }

  async type(locator, text, { delay = 75 } = {}) {
    await this.click(locator, { pause: 200 });
    await locator.pressSequentially(text, { delay });
    await sleep(400);
  }

  // 문서를 부드럽게 스크롤. to: 'bottom'(푸터 포함) | 'footer'(푸터 직전) | 'top' | 숫자(px)
  // requestAnimationFrame 은 헤드리스에서 멈출 때가 있어(2시간 정지 사고) 타이머로 움직이고,
  // 페이지 쪽 대기는 최대 20초로 끊는다(page.evaluate 에는 자체 타임아웃이 없다).
  async scroll(to, { speed = 700 } = {}) {
    const job = this.page.evaluate(async ([to, speed]) => {
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
    }, [to, speed]);
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

async function closeRecorder({ browser, context, page }) {
  const video = page.video();
  await context.close();
  await browser.close();
  return video ? await video.path() : null;
}

module.exports = { SITE, VIEW, OUT, sleep, log, withTimeout, openRecorder, closeRecorder };
