// 시연영상 녹화. 사용법: node record.js A|B  (세그먼트를 따로 찍어 나중에 이어 붙인다)
//  A: 제목 → 비로그인 홈 → AI은행원(칩·추천·은행 사이트·피드백) → 이체 요청 → 로그인 → AI 이체 실행
//  B: demo — 내 계좌(거래내역·이체) → 상품안내 → 고객센터 → 마이페이지 → 로그아웃
//     → admin 로그인 → 백오피스 9개 메뉴 → 제목(끝). 로그아웃~로그인 이음매가 없도록 한 번에 찍는다
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
    if ((await likeCount(page)) > prev) { await sleep(400); return; }
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
  // AI은행원(Streamlit)은 처음 뜰 때 몇 초 걸린다 — 제목 카드 전에 한 번 띄워 두고 홈으로 돌아와서,
  // 히어로 버튼을 누르면 바로 나오게 한다(이 구간은 시작점 앞이라 잘려 나간다).
  // iframe 은 섹션이 보일 때 띄워야 입력창이 한 줄형으로 잡힌다(CLAUDE.md) — 그래서 실제로 #chat 을 보여 준다
  await page.evaluate(() => { location.hash = '#chat'; });
  await waitChatReady(page);
  await page.evaluate(() => { location.hash = '#home'; });
  await sleep(1000);
  await page.evaluate(() => scrollTo(0, 0));
  await d.restore();
  const hideTitle = await d.title(1, 2400);   // 1번 자막은 영상 첫 화면 가운데 제목으로(사라지는 시간까지 약 3초)
  await d.markStart(); await hideTitle();
  await sleep(800);

  await d.scroll('bottom', { speed: 600 }); await sleep(1200);
  await d.scroll('top', { speed: 1400 });

  await d.caption(2); await sleep(1200);
  await d.click(page.locator('.hero-cta [data-nav="chat"]'));
  await waitChatReady(page);   // 미리 띄워 둬서 바로 나온다(waitChatReady 안의 1.5초만 머문다)
  const chat = page.frameLocator(CHAT_IFRAME);

  // 칩은 입력창에 포커스해야 보인다 — 칩이 뜬 뒤 자막
  await d.click(chat.locator('textarea').first(), { pause: 500 });
  await d.caption(3); await d.waitCaption();
  let n = await likeCount(page);
  await d.click(chat.locator('[class*="st-key-sugg_"] button', { hasText: '적금 상품 비교해줘' }), { pause: 500 });
  await d.moveTo(1300, 640);
  await waitAnswer(page, n);
  await d.caption(4);   // 칩 질문의 답변(조건 확인)이 뜨자마자
  await chatScroll(page, 'bottom'); await d.waitCaption();

  n = await likeCount(page);
  await d.type(chat.locator('textarea').first(), '12개월 만기 적금 중 금리가 높은 상품으로 5개 추천해줘', { delay: 70 });
  await chat.locator('textarea').first().press('Enter');
  await d.moveTo(1300, 640);
  await waitAnswer(page, n);

  await d.caption(5);
  // 새 답변의 맨 위부터 천천히 내려 본다
  const f = chatFrame(page);
  await f.evaluate(() => { const m = [...document.querySelectorAll('[data-testid="stChatMessage"]')].pop(); m.scrollIntoView({ block: 'start' }); });
  await sleep(1500);
  await chatScroll(page, 350, 300); await d.waitCaption();

  // "홈페이지에서 검색하기": 케이뱅크 상품 링크(없으면 첫 링크)를 눌러 은행 사이트로 넘어가는 모습을 보여 준다
  const links = chat.locator('a', { hasText: '홈페이지에서 검색하기' });
  const idx = await links.evaluateAll((as) => Math.max(0, as.findIndex((a) =>
    /케이뱅크/.test((a.closest('li') || a.parentElement || a).textContent))));
  const link = links.nth(idx);
  await link.scrollIntoViewIfNeeded(); await sleep(400);
  const b = await link.boundingBox();
  await d.moveTo(Math.round(b.x + b.width / 2), Math.round(b.y + b.height / 2));
  await link.hover();
  await d.caption(6); await d.waitCaption();
  await d.clickPopup(link);
  await sleep(1200);
  await chatScroll(page, 'bottom', 400); await sleep(1200);

  // 자막을 먼저 띄우고 다 읽은 뒤 누른다(👎·이유·제출 버튼이 대화 맨 아래 자막 자리에 있어 겹치지 않게)
  await d.caption(7); await d.waitCaption();
  // st 버튼(px_dislike_)은 화면에 안 보이는 대리 버튼이라 커서가 엉뚱한 곳을 누르는 것처럼 보였다 — 보이는 👎 아이콘을 누른다
  await d.click(chat.locator('[title="싫어요"]').last(), { pause: 1200 });
  await d.click(chat.getByText('원하는 답변이 아님').last(), { pause: 800 });
  await d.click(chat.locator('[class*="st-key-dislike_submit_"] button').last(), { pause: 1200 });

  // 이체 요청(비로그인) → 로그인 안내 → 로그인 → 같은 대화로 복귀
  n = await likeCount(page);
  await d.type(chat.locator('textarea').first(), '이영희한테 5만원 보내줘', { delay: 80 });
  await chat.locator('textarea').first().press('Enter');
  await d.moveTo(1300, 640);
  await waitAnswer(page, n);
  await d.caption(8);   // 답변(로그인 안내)과 함께 한 번만
  await chatScroll(page, 'bottom'); await d.waitCaption();
  let loginBtn = null;
  for (let i = 0; i < 20 && !loginBtn; i++) {
    for (const fr of page.frames()) {
      const l = fr.locator('button', { hasText: '로그인하기' });
      if (await l.count().catch(() => 0)) { loginBtn = l.first(); break; }
    }
    if (!loginBtn) await sleep(500);
  }
  await d.click(loginBtn, { pause: 1500 });
  await d.restore();
  await login(page, d, DEMO);
  await d.restore();
  await waitChatReady(page);
  await sleep(1500);

  // 로그인하고 돌아오면 AI은행원이 멈췄던 요청을 스스로 이어 가 확인 카드를 띄운다(다시 입력하지 않는다)
  const chat2 = page.frameLocator(CHAT_IFRAME);
  await chat2.getByText('이체할까요?').last().waitFor({ timeout: 150000 });
  await sleep(1000);
  await chat2.getByText('이체할까요?').last().scrollIntoViewIfNeeded();
  await d.caption(9); await d.waitCaption();

  await d.caption(10);
  // 자주쓰는 계좌 칩 → 은행·계좌번호 입력칸을 차례로 짚는다(받는 사람은 바꾸지 않는다: 누르면 받는 사람이 바뀐다)
  await d.hover(chat2.getByRole('button', { name: /김철수 · / }).first(), { pause: 1500 });
  await d.click(chat2.getByLabel('받는 분 계좌번호'), { pause: 1500 });
  await d.click(chat2.getByLabel('이체 금액 (원)'), { pause: 1000 });
  await d.waitCaption();

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

// 마이페이지 예약이체에 보일 예약 1건: 내일 09:00(KST) 박민수(월세)에게 30만원. 녹화 후 DB 복원으로 사라진다
async function addScheduledTransfer(page, token) {
  const H = { Authorization: `Bearer ${token}` };
  if ((await (await page.request.get(SITE + '/api/me/scheduled-transfers', { headers: H })).json()).transfers.length) return;
  const from = (await (await page.request.get(SITE + '/api/accounts', { headers: H })).json()).accounts[0].account_no;
  const fav = (await (await page.request.get(SITE + '/api/me/favorites', { headers: H })).json()).favorites
    .find((f) => f.holder_name === '박민수');
  const KST = 9 * 3600 * 1000;
  const d = new Date(Date.now() + KST); d.setUTCDate(d.getUTCDate() + 1); d.setUTCHours(9, 0, 0, 0);
  const r = await page.request.post(SITE + '/api/transfer', { headers: H, data: {
    from_account: from, to_account: fav.account_no, amount: 300000, memo: '월세', password: DEMO[2],
    scheduled_at: (d.getTime() - KST) / 1000 } });
  log(`예약이체 준비: ${r.status()} ${(await r.text()).slice(0, 120)}`);
}

// ── B (+ 백오피스까지 이어서) ────────────────────────────────────────
async function segB(page, d) {
  // demo 로그인 상태로 시작(세그먼트 A 끝과 이어지게 AI은행원 화면에서 출발)
  const auth = await (await page.request.post(SITE + '/api/login', { data: { username: DEMO[0], password: DEMO[1] } })).json();
  await addScheduledTransfer(page, auth.token);
  await page.goto(SITE + '/');
  await page.evaluate((a) => localStorage.setItem('auth', JSON.stringify(a)), auth);
  await page.goto(SITE + '/#account'); await page.reload({ waitUntil: 'networkidle' });
  await d.restore(); await d.markStart(); await sleep(1000);

  // 내 계좌
  await d.caption(12); await sleep(1500);
  await d.click(page.locator('.acct-tile-history').first(), { pause: 1800 });
  await d.scroll('footer', { speed: 500 }); await d.waitCaption();
  await d.scroll('top', { speed: 1200 });

  // 이체: 천천히, 푸터까지 내려가지 않게
  await d.click(page.locator('.account-tab[data-account-tab="transfer"]'), { pause: 600 });
  await d.caption(13);
  await d.click(page.locator('.tf-quick-acct', { hasText: '김철수' }), { pause: 2200 });
  const ok = page.locator('#modal-confirm-ok');
  if (await ok.isVisible().catch(() => false)) await d.click(ok, { pause: 800 });
  await d.waitCaption();

  await d.caption(14);
  await d.type(page.locator('#tf-amount'), '30000', { delay: 120 });
  await sleep(800);
  await d.click(page.locator('#tf-to-confirm'), { pause: 300 });   // 화면이 짧아져 푸터가 보이기 전에 바로 올린다
  await d.scroll(await page.locator('#tf-step-2').evaluate((e) => e.getBoundingClientRect().top + scrollY - 120), { speed: 350, noFooter: true });
  await sleep(1800);
  await d.click(page.locator('#tf-confirm-check'), { pause: 600 });
  await d.type(page.locator('#tf-password'), DEMO[2], { delay: 150 });
  await d.click(page.locator('#tf-submit'), { pause: 500 });
  await page.locator('#tf-step-3').waitFor({ state: 'visible', timeout: 60000 });
  await page.locator('#tf-receipt').getByText(/완료|영수증|거래/).first().waitFor({ timeout: 60000 }).catch(() => {});
  await sleep(800);
  await d.scroll(await page.locator('#tf-step-3').evaluate((e) => e.getBoundingClientRect().top + scrollY - 120), { speed: 350, noFooter: true });
  await sleep(3500);
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

  // 은행 바로가기: 목록으로 내려간 뒤 자막, 다 읽은 다음 은행 사이트로(돌아올 때 다시 뜨지 않음)
  let first = true;
  for (const bank of ['KB국민은행', '우리은행', '토스뱅크']) {
    const chip = page.locator(`#bank-grid a.bank-chip[data-bank="${bank}"]`);
    await chip.evaluate((a) => a.setAttribute('target', '_self'));   // 녹화 중에만 같은 탭으로
    await d.scroll(await chip.evaluate((e) => e.getBoundingClientRect().top + scrollY - 300), { speed: 900 });
    if (first) { await d.caption(17); await d.waitCaption(); first = false; }
    await d.click(chip, { pause: 0 });
    await page.waitForLoadState('domcontentloaded', { timeout: 30000 }).catch(() => {});
    await d.restore(); await sleep(4000);
    await page.goBack({ waitUntil: 'networkidle' }).catch(() => {});
    await d.restore(); await sleep(1500);
  }

  // 고객센터
  await nav(page, d, 'support');
  await d.caption(18);
  for (const t of ['notices', 'faq', 'inquiry', 'documents', 'events']) {
    await d.click(page.locator(`.support-tab[data-support-tab="${t}"]`), { pause: 1500 });   // 탭 화면만, 스크롤 없이
  }
  await d.caption(null);

  // 마이페이지: 탭 화면이 나온 뒤 자막(회원탈퇴는 보여 주지 않음)
  await d.click(page.locator('#nav-user-name'), { pause: 1500 });
  await d.restore();
  const tabs = [['profile', 'M1'], ['security', 'M2'], ['accounts', 'M3'], ['favorites', 'M4'],
                ['scheduled', 'M5'], ['inquiries', 'M6'], ['statement', 'M7']];
  for (const [t, cap] of tabs) {
    await d.click(page.locator(`.mypage-tab[data-mypage-tab="${t}"]`), { pause: 500 });
    await d.caption(cap);
    await d.scroll('footer', { speed: 600 }); await sleep(1000);
    await d.scroll('top', { speed: 1400 });
    await d.waitCaption();
  }

  // 로그아웃 → 홈에서 19번 자막 → 바로 admin 로그인 → 백오피스
  await d.click(page.locator('#nav-logout'), { pause: 1200 });
  await d.restore();
  await d.caption(19);
  await d.click(page.locator('#nav-auth-guest .nav-login'), { pause: 1000 });
  await d.restore();
  await login(page, d, ADMIN);
  await d.restore();
  await d.click(page.locator('#nav-backoffice-link'), { pause: 1500 });
  await d.restore();
  await d.waitCaption();

  const menus = [['dashboard', 20], ['members', 21], ['transfers', 22], ['usage', 23], ['perf', 24],
                 ['prompt', 25], ['products', 26], ['faq', 27], ['banners', 28]];
  for (const [t, cap] of menus) {
    await d.click(page.locator(`.bo-tab[data-bo-tab="${t}"]`), { pause: 700 });
    await d.caption(cap);   // 메뉴 화면이 나온 뒤
    if (t === 'transfers') {
      // 위에서부터 천천히 내려가며 보여 준다(필터 버튼까지 → 완료 필터 → 끝까지)
      const f = page.locator('.bo-filter-btn[data-status="completed"]');
      if (await f.count()) {
        await d.scroll(await f.first().evaluate((e) => e.getBoundingClientRect().top + scrollY - 300), { speed: 300 });
        await d.click(f.first(), { pause: 1500 });
      }
      await d.scroll('footer', { speed: 300 }); await sleep(1500);
    } else {
      await d.scroll('footer', { speed: 450 }); await sleep(1500);
    }
    await d.waitCaption();
    await d.scroll('top', { speed: 1400 });
  }
  await d.caption(null); await sleep(800);

  // 마지막은 첫 화면과 같은 제목 카드로
  await d.title(1, 0, { fade: true });
  await sleep(3500);
}

(async () => {
  const seg = process.argv[2];
  const run = { A: segA, B: segB }[seg];
  if (!run) throw new Error('usage: node record.js A|B');
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
