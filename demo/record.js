// 시연영상 녹화. 사용법: node record.js A|B|C  (각 세그먼트를 따로 찍어 나중에 이어 붙인다)
//  A: 비로그인 홈 → AI은행원(칩·추천·피드백) → 이체 요청 → 로그인 → AI 이체 실행
//  B: demo — 내 계좌(거래내역·이체) → 상품안내 → 고객센터 → 마이페이지 → 로그아웃
//  C: admin 로그인 → 백오피스 9개 메뉴
const { SITE, sleep, log, withTimeout, openRecorder, closeRecorder } = require('./lib');

const CHAT_IFRAME = 'iframe[title="AI 금융상담 은행원"]';
const DEMO = ['demo', 'demo1234', '246810'];

const ADMIN = ['admin', 'admin1234'];

const chatFrame = (page) => page.frames().find((f) => f.url().includes(':8501') && f.url().includes('embed'))
  || page.frames().find((f) => f.url().includes(':8501'));

async function waitChatReady(page) {
  await page.frameLocator(CHAT_IFRAME).locator('textarea').first().waitFor({ timeout: 90000 });
  await sleep(1500);
}

// 답변이 끝나면 피드백 버튼(👍)이 하나 더 생긴다 — 그걸 완료 신호로 쓴다
async function likeCount(page) {
  return page.frameLocator(CHAT_IFRAME).locator('[class*="st-key-px_like_"]').count();
}
async function waitAnswer(page, prev, timeout = 150000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    if ((await likeCount(page)) > prev) { await sleep(1200); return; }
    await sleep(700);
  }
  throw new Error('AI 답변 대기 시간 초과');
}

// AI은행원 iframe 안의 대화 영역을 부드럽게 스크롤
async function chatScroll(page, to, speed = 500) {
  const f = chatFrame(page);
  await withTimeout(f.evaluate(async ([to, speed]) => {
    const els = [...document.querySelectorAll('*')].filter((e) => {
      const s = getComputedStyle(e);
      return /(auto|scroll)/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 20 && e.clientHeight > 300;
    });
    const el = els.sort((a, b) => b.clientHeight - a.clientHeight)[0] || document.scrollingElement;
    const max = el.scrollHeight - el.clientHeight;
    const target = to === 'bottom' ? max : to === 'top' ? 0 : Math.max(0, Math.min(max, el.scrollTop + to));
    const from = el.scrollTop, dist = target - from, dur = Math.max(400, Math.abs(dist) / speed * 1000);
    await new Promise((res) => {
      const t0 = performance.now();
      const step = () => { const k = Math.min(1, (performance.now() - t0) / dur); const e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
        el.scrollTop = from + dist * e; k < 1 ? setTimeout(step, 16) : res(); };
      step();
    });
  }, [to, speed]), 20000, 'chatScroll');
  await sleep(500);
}

async function login(page, d, [user, pw]) {
  await page.locator('#login-username').waitFor();
  await d.type(page.locator('#login-username'), user);
  await d.type(page.locator('#login-password'), pw, { delay: 60 });
  await d.click(page.locator('#login-form button[type="submit"]'), { pause: 1500 });
}

async function nav(page, d, name) {
  await d.click(page.locator(`nav [data-nav="${name}"]`).first(), { pause: 1500 });
  await d.restore();
}

// ── A ────────────────────────────────────────────────────────────────
async function segA(page, d) {
  await page.goto(SITE + '/');
  await page.evaluate(() => localStorage.removeItem('auth'));
  await page.reload({ waitUntil: 'networkidle' });
  await d.restore();
  await sleep(800);

  await d.caption(1); await sleep(2500);
  await d.scroll('bottom', { speed: 600 }); await sleep(1200);
  await d.scroll('top', { speed: 1400 }); await d.caption(null);

  await d.caption(2); await sleep(1200);
  await d.click(page.locator('.hero-cta [data-nav="chat"]'));
  await waitChatReady(page);
  d.setCaptionTop(true);   // AI은행원 화면은 입력창이 아래에 있어 자막을 위로
  await d.caption(2);
  const chat = page.frameLocator(CHAT_IFRAME);

  // 칩은 입력창에 포커스해야 보인다
  await d.caption(3);
  await d.click(chat.locator('textarea').first(), { pause: 1200 });
  let n = await likeCount(page);
  await d.click(chat.locator('[class*="st-key-sugg_"] button', { hasText: '적금 상품 비교해줘' }), { pause: 500 });
  await d.moveTo(1300, 760);
  await waitAnswer(page, n);
  await chatScroll(page, 'bottom'); await sleep(2500);

  await d.caption(4);
  n = await likeCount(page);
  await d.type(chat.locator('textarea').first(), '12개월 만기 적금 중 금리가 높은 상품으로 5개 추천해줘', { delay: 70 });
  await chat.locator('textarea').first().press('Enter');
  await d.moveTo(1300, 760);
  await waitAnswer(page, n);

  await d.caption(5);
  // 새 답변의 맨 위부터 천천히 내려 본다
  const f = chatFrame(page);
  await f.evaluate(() => { const m = [...document.querySelectorAll('[data-testid="stChatMessage"]')].pop(); m.scrollIntoView({ block: 'start' }); });
  await sleep(1500);
  await chatScroll(page, 350, 300); await sleep(1500);

  await d.caption(6);
  const link = chat.locator('a', { hasText: '홈페이지에서 검색하기' }).first();
  await link.scrollIntoViewIfNeeded(); await sleep(400);
  const b = await link.boundingBox();
  await d.moveTo(Math.round(b.x + b.width / 2), Math.round(b.y + b.height / 2));
  await link.hover(); await sleep(2500);
  await chatScroll(page, 'bottom', 400); await sleep(1200);

  await d.caption(7);
  await d.click(chat.locator('[class*="st-key-px_dislike_"] button').last(), { pause: 1500 });
  await d.click(chat.getByText('원하는 답변이 아님').last(), { pause: 1000 });
  await d.click(chat.locator('[class*="st-key-dislike_submit_"] button').last(), { pause: 2000 });
  await d.caption(null);

  // 이체 요청(비로그인) → 로그인 안내 → 로그인 → 같은 대화로 복귀
  await d.caption(8);
  n = await likeCount(page);
  await d.type(chat.locator('textarea').first(), '이영희한테 5만원 보내줘', { delay: 80 });
  await chat.locator('textarea').first().press('Enter');
  await d.moveTo(1300, 760);
  await waitAnswer(page, n);
  await chatScroll(page, 'bottom'); await sleep(1500);
  let loginBtn = null;
  for (let i = 0; i < 20 && !loginBtn; i++) {
    for (const fr of page.frames()) {
      const l = fr.locator('button', { hasText: '로그인하기' });
      if (await l.count().catch(() => 0)) { loginBtn = l.first(); break; }
    }
    if (!loginBtn) await sleep(500);
  }
  d.setCaptionTop(false);
  await d.click(loginBtn, { pause: 1500 });
  await d.restore();
  await login(page, d, DEMO);
  d.setCaptionTop(true);
  await d.restore();
  await waitChatReady(page);
  await sleep(1500);

  await d.caption(9);
  const chat2 = page.frameLocator(CHAT_IFRAME);
  if (!(await chat2.getByText('이체할까요?').count())) {
    n = await likeCount(page);
    await d.type(chat2.locator('textarea').first(), '이영희한테 5만원 보내줘', { delay: 80 });
    await chat2.locator('textarea').first().press('Enter');
    await d.moveTo(1300, 760);
  }
  await chat2.getByText('이체할까요?').last().waitFor({ timeout: 150000 });
  await sleep(1500);
  await chat2.getByText('이체할까요?').last().scrollIntoViewIfNeeded();
  await sleep(2000);

  await d.caption(10);
  await d.click(chat2.locator('[data-testid="stSelectbox"]', { hasText: '받는 계좌' }), { pause: 2200 });
  await page.keyboard.press('Escape'); await sleep(800);
  await d.click(chat2.getByLabel('이체 금액 (원)'), { pause: 1500 });

  await d.caption(11);
  await d.click(chat2.getByRole('button', { name: '이체 확인' }), { pause: 1800 });
  await d.click(chat2.getByText('받는 분(예금주명)과 금액을 확인했습니다'), { pause: 700 });
  await d.type(chat2.getByLabel('이체 비밀번호 (숫자 6자리)'), DEMO[2], { delay: 150 });
  await chat2.getByLabel('이체 비밀번호 (숫자 6자리)').press('Enter'); await sleep(600);
  await d.click(chat2.getByRole('button', { name: '이체하기' }), { pause: 500 });
  await chat2.getByText('이체 완료').last().waitFor({ timeout: 60000 });
  await sleep(1000);
  await chatScroll(page, 'bottom'); await sleep(3500);
  await d.caption(null); await sleep(600);
}

// ── B ────────────────────────────────────────────────────────────────
async function segB(page, d) {
  // demo 로그인 상태로 시작(세그먼트 A 끝과 이어지게 AI은행원 화면에서 출발)
  const auth = await (await page.request.post(SITE + '/api/login', { data: { username: DEMO[0], password: DEMO[1] } })).json();
  await page.goto(SITE + '/');
  await page.evaluate((a) => localStorage.setItem('auth', JSON.stringify(a)), auth);
  await page.goto(SITE + '/#account'); await page.reload({ waitUntil: 'networkidle' });
  await d.restore(); await sleep(1500);

  // 내 계좌
  await d.caption(12); await sleep(1500);
  await d.click(page.locator('.acct-tile-history').first(), { pause: 1800 });
  await d.scroll('footer', { speed: 500 }); await sleep(1500);
  await d.scroll('top', { speed: 1200 });

  await d.caption(13);
  await d.click(page.locator('.account-tab[data-account-tab="transfer"]'), { pause: 1500 });
  await d.click(page.locator('.tf-quick-acct', { hasText: '김철수' }), { pause: 2200 });
  const ok = page.locator('#modal-confirm-ok');
  if (await ok.isVisible().catch(() => false)) await d.click(ok, { pause: 800 });

  await d.caption(14);
  await d.type(page.locator('#tf-amount'), '30000', { delay: 120 });
  await sleep(800);
  await d.click(page.locator('#tf-to-confirm'), { pause: 2000 });
  await d.click(page.locator('#tf-confirm-check'), { pause: 600 });
  await d.type(page.locator('#tf-password'), DEMO[2], { delay: 150 });
  await d.click(page.locator('#tf-submit'), { pause: 500 });
  await page.locator('#tf-step-3').waitFor({ state: 'visible', timeout: 60000 });
  await page.locator('#tf-receipt').getByText(/완료|영수증|거래/).first().waitFor({ timeout: 60000 }).catch(() => {});
  await sleep(1000);
  await d.scroll('footer', { speed: 500 }); await sleep(2500);
  await d.caption(null);

  // 상품안내
  await nav(page, d, 'products');
  await d.caption(15);
  await d.scroll(await page.locator('#stat-products').evaluate((e) => e.getBoundingClientRect().top + scrollY - 160));
  await sleep(1500);
  await d.click(page.locator('#stat-products li[data-product]').first(), { pause: 3500 });

  await d.caption(16);
  for (const cat of ['예금', '적금', '주택담보대출', '전세자금대출', '신용대출']) {
    const tab = page.locator(`.cat-tab[data-category="${cat}"]`);
    await d.click(tab, { pause: 1500 });
    await d.scroll(await page.locator('#product-list-panel').evaluate((e) => e.getBoundingClientRect().top + scrollY - 140), { speed: 900 });
    await sleep(1600);
    await d.scroll(await tab.evaluate((e) => e.getBoundingClientRect().top + scrollY - 200), { speed: 1200 });
  }

  await d.caption(17);
  for (const bank of ['KB국민은행', '우리은행', '토스뱅크']) {
    const chip = page.locator(`#bank-grid a.bank-chip[data-bank="${bank}"]`);
    await chip.evaluate((a) => a.setAttribute('target', '_self'));   // 녹화 중에만 같은 탭으로
    await d.scroll(await chip.evaluate((e) => e.getBoundingClientRect().top + scrollY - 300), { speed: 900 });
    await d.click(chip, { pause: 0 });
    await page.waitForLoadState('domcontentloaded', { timeout: 30000 }).catch(() => {});
    await d.restore(); await sleep(4000);
    await page.goBack({ waitUntil: 'networkidle' }).catch(() => {});
    await d.restore(); await sleep(1500);
  }
  await d.caption(null);

  // 고객센터
  await nav(page, d, 'support');
  await d.caption(18);
  for (const t of ['notices', 'faq', 'inquiry', 'documents', 'events']) {
    await d.click(page.locator(`.support-tab[data-support-tab="${t}"]`), { pause: 1200 });
    await d.scroll('footer', { speed: 700 }); await sleep(1000);
    await d.scroll('top', { speed: 1400 });
  }
  await d.caption(null);

  // 마이페이지
  await d.click(page.locator('#nav-user-name'), { pause: 1500 });
  await d.restore();
  const tabs = [['profile', 'M1'], ['security', 'M2'], ['accounts', 'M3'], ['favorites', 'M4'],
                ['scheduled', 'M5'], ['inquiries', 'M6'], ['statement', 'M7'], ['withdraw', null]];
  for (const [t, cap] of tabs) {
    await d.caption(cap);
    await d.click(page.locator(`.mypage-tab[data-mypage-tab="${t}"]`), { pause: 1200 });
    await d.scroll('footer', { speed: 600 }); await sleep(1200);
    await d.scroll('top', { speed: 1400 });
  }
  await d.caption(null);

  // 로그아웃
  await d.click(page.locator('#nav-logout'), { pause: 2000 });
  await d.restore(); await sleep(1000);
}

// ── C ────────────────────────────────────────────────────────────────
async function segC(page, d) {
  await page.goto(SITE + '/');
  await page.evaluate(() => localStorage.removeItem('auth'));
  await page.reload({ waitUntil: 'networkidle' });
  await d.restore(); await sleep(1000);

  await d.click(page.locator('#nav-auth-guest .nav-login'), { pause: 1200 });
  await d.restore();
  await login(page, d, ADMIN);
  await d.restore(); await sleep(1000);

  await d.caption(19); await sleep(1500);
  await d.click(page.locator('#nav-backoffice-link'), { pause: 2000 });
  await d.restore();

  const menus = [['dashboard', 20], ['members', 21], ['transfers', 22], ['usage', 23], ['perf', 24],
                 ['prompt', 25], ['products', 26], ['faq', 27], ['banners', 28]];
  for (const [t, cap] of menus) {
    await d.caption(cap);
    await d.click(page.locator(`.bo-tab[data-bo-tab="${t}"]`), { pause: 2200 });
    if (t === 'transfers') {
      const f = page.locator('.bo-filter-btn[data-status="completed"]');
      if (await f.count()) { await d.click(f.first(), { pause: 1500 }); }
    }
    await d.scroll('footer', { speed: 450 }); await sleep(1500);
    await d.scroll('top', { speed: 1400 });
  }
  await d.caption(null); await sleep(800);
}

(async () => {
  const seg = process.argv[2];
  const run = { A: segA, B: segB, C: segC }[seg];
  if (!run) throw new Error('usage: node record.js A|B|C');
  const rec = await openRecorder('seg' + seg);
  try {
    await run(rec.page, rec.d);
  } catch (e) {
    await rec.page.screenshot({ path: `out/fail-${seg}.png` }).catch(() => {});
    console.error('FAILED', seg, e.message);
    process.exitCode = 1;
  }
  console.log(await closeRecorder(rec));
})();
