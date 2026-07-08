/* ── 매치뱅크 홈페이지 클라이언트 로직 ───────────────────────────
 * - 네비게이션: 헤더 고정, 본문 <section>만 토글 (SPA형)
 * - AI챗봇: 최초 진입 시 iframe 1회 생성 후 유지 (대화 상태 보존)
 * - 대시보드/은행 목록: data/*.json fetch 후 렌더
 * - FAQ: 아코디언 토글
 */

const CHAT_URL = "http://localhost:8501/?embed=true&embedded=1";
const SECTIONS = ["home", "account", "products", "chat", "support", "auth", "backoffice"];

/* ── 인증 상태 (localStorage) ───────────────────────────────────────
 * auth = { token, username, name, role }
 */
function getAuth() {
  try {
    return JSON.parse(localStorage.getItem("auth"));
  } catch {
    return null;
  }
}
function setAuth(data) {
  localStorage.setItem("auth", JSON.stringify(data));
}
function clearAuth() {
  localStorage.removeItem("auth");
}
function isLoggedIn() {
  return !!getAuth()?.token;
}

/* 인증 헤더 자동 첨부 + 401 시 로그아웃 처리하는 fetch 래퍼 */
async function apiFetch(url, opts = {}) {
  const auth = getAuth();
  const headers = { ...(opts.headers || {}) };
  if (auth?.token) headers["Authorization"] = "Bearer " + auth.token;
  const res = await fetch(url, { ...opts, headers });
  if (res.status === 401) {
    clearAuth();
    refreshAuthUI();
    navigate("auth");
  }
  return res;
}

/* 로그인 상태에 따라 nav + #auth 섹션 표시 갱신 */
function refreshAuthUI() {
  const auth = getAuth();
  const logged = !!auth?.token;
  document.getElementById("nav-auth-guest").style.display = logged ? "none" : "";
  document.getElementById("nav-auth-user").style.display = logged ? "" : "none";
  if (logged) document.getElementById("nav-user-name").textContent = `${auth.name}님`;
  const isAdmin = logged && auth.role === "admin";
  document.getElementById("nav-backoffice-sep").style.display = isAdmin ? "" : "none";
  document.getElementById("nav-backoffice-link").style.display = isAdmin ? "" : "none";
  updateAuthSectionView();
  syncChatAuth();   // 로그인/로그아웃을 챗봇 iframe에 반영(사이트 로그인과 연동)
}

function updateAuthSectionView() {
  const auth = getAuth();
  const logged = !!auth?.token;
  const loginPanel = document.getElementById("login-panel");
  const signupPanel = document.getElementById("signup-panel");
  document.getElementById("auth-welcome").style.display = logged ? "" : "none";
  if (logged) {
    loginPanel.style.display = "none";
    signupPanel.style.display = "none";
    document.getElementById("auth-welcome-title").textContent = `환영합니다, ${auth.name}님`;
    return;
  }
  // 비로그인: 둘 다 숨겨져 있으면 기본 로그인 화면 표시(로그아웃 직후 등)
  if (loginPanel.style.display === "none" && signupPanel.style.display === "none") {
    setAuthTab("login");
  }
}

/* 로그인 화면 ↔ 회원가입 화면 전환 (탭 제거 후 독립 패널 토글) */
function setAuthTab(tab) {
  document.getElementById("login-panel").style.display = tab === "login" ? "" : "none";
  document.getElementById("signup-panel").style.display = tab === "signup" ? "" : "none";
}

/* ── 범용 모달 ───────────────────────────────────────────────────── */
function showModal(html) {
  document.getElementById("modal-body").innerHTML = html;
  document.getElementById("modal-overlay").style.display = "flex";
}
function closeModal() {
  document.getElementById("modal-overlay").style.display = "none";
}
document.addEventListener("click", (e) => {
  if (e.target.id === "modal-overlay" || e.target.closest("#modal-close") || e.target.closest("#modal-confirm-ok")) {
    closeModal();
  }
});

/* ── 라우팅 ──────────────────────────────────────────────────────── */
function navigate(name) {
  if (!SECTIONS.includes(name)) name = "home";

  if (name !== "backoffice") stopBoAutoRefresh();   // 관리자 밖으로 나가면 자동 갱신 해제

  document.querySelectorAll(".section").forEach((s) => {
    s.classList.toggle("active", s.id === name);
  });
  document.querySelectorAll(".nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.nav === name);
  });
  updateNavIndicator();

  if (name === "chat") ensureChatLoaded();
  if (name === "products") { ensureBanksLoaded(); loadProductStats(); }
  if (name === "account") {
    const logged = isLoggedIn();
    document.getElementById("account-guest").style.display = logged ? "none" : "";
    document.getElementById("account-authed").style.display = logged ? "" : "none";
    if (logged) { accountGoTab("inquiry"); loadAccounts(); }
  }
  if (name === "backoffice") {
    const auth = getAuth();
    if (!auth?.token || auth.role !== "admin") {
      navigate("home");
      return;
    }
    ensureBoTabLoaded("dashboard");
    const activeTab = document.querySelector(".bo-tab.active");
    setBoAutoRefresh(activeTab ? activeTab.dataset.boTab : "dashboard");
  }
  if (name === "support") {
    const active = document.querySelector(".support-tab.active");
    ensureSupportTabLoaded(active ? active.dataset.supportTab : "notices");
  }

  // 해시 동기화 (뒤로가기 지원)
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  window.scrollTo(0, 0);
}

/* 헤더 메뉴 하단 슬라이딩 인디케이터: 선택된 메뉴 아래로 부드럽게 이동 */
function updateNavIndicator() {
  const indicator = document.getElementById("nav-indicator");
  const active = document.querySelector(".nav > a.active");
  if (!indicator || !active || active.offsetParent === null) {
    if (indicator) indicator.style.opacity = "0";
    return;
  }
  indicator.style.left = `${active.offsetLeft}px`;
  indicator.style.width = `${active.offsetWidth}px`;
  indicator.style.opacity = "1";
}
window.addEventListener("resize", updateNavIndicator);

/* 클릭 위임: data-nav 속성을 가진 모든 요소 (data-auth 있으면 로그인/회원가입 탭도 맞춤) */
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-nav]");
  if (!el) return;
  e.preventDefault();
  navigate(el.dataset.nav);
  if (el.dataset.auth) setAuthTab(el.dataset.auth);
});

/* ── 홈: 이벤트 배너 자동 전환 ───────────────────────────────────── */
const BANNER_INTERVAL = 4000;
let bannerIndex = 0;
let bannerTimer = null;

function goBannerSlide(i) {
  const track = document.getElementById("banner-track");
  const dots = document.querySelectorAll(".banner-dot");
  if (!track || !dots.length) return;
  bannerIndex = (i + dots.length) % dots.length;
  track.style.transform = `translateX(-${bannerIndex * 100}%)`;
  dots.forEach((d, idx) => d.classList.toggle("active", idx === bannerIndex));
}

function startBannerAuto() {
  stopBannerAuto();
  bannerTimer = setInterval(() => goBannerSlide(bannerIndex + 1), BANNER_INTERVAL);
}
function stopBannerAuto() {
  if (bannerTimer) clearInterval(bannerTimer);
}

document.addEventListener("click", (e) => {
  const dot = e.target.closest(".banner-dot");
  if (!dot) return;
  goBannerSlide(Number(dot.dataset.slide));
  startBannerAuto();
});

const bannerEl = document.querySelector(".event-banner");
if (bannerEl) {
  bannerEl.addEventListener("mouseenter", stopBannerAuto);
  bannerEl.addEventListener("mouseleave", startBannerAuto);
  startBannerAuto();
}

/* ── AI챗봇 iframe: 최초 1회만 생성 ─────────────────────────────── */
let chatLoaded = false;

/* 챗봇 iframe URL: 사이트 로그인 토큰을 항상 token 파라미터로 전달(로그아웃 시 빈 값).
   → 챗봇(:8501)이 사이트 로그인 상태를 단일 기준으로 삼아 동기화한다.
   (로컬 데모용. 프로덕션에서는 URL 대신 postMessage 등 안전한 방식 권장) */
function chatSrc() {
  const token = getAuth()?.token || "";
  return `${CHAT_URL}&token=${encodeURIComponent(token)}`;
}

function ensureChatLoaded() {
  if (chatLoaded) return;
  const wrap = document.getElementById("chat-frame-wrap");
  const iframe = document.createElement("iframe");
  iframe.id = "chat-frame";
  iframe.src = chatSrc();
  iframe.title = "AI 금융상담 챗봇";
  iframe.allow = "clipboard-write";
  wrap.appendChild(iframe);
  chatLoaded = true;
}

/* 로그인/로그아웃 시 챗봇 iframe을 현재 토큰으로 재로딩 → 로그인 상태 연동 */
function syncChatAuth() {
  const iframe = document.getElementById("chat-frame");
  if (iframe) iframe.src = chatSrc();
}

/* ── 은행 로고 배지 ──────────────────────────────────────────────── */
/* 실제 로고 이미지 대신 은행 브랜드 컬러 기반 이니셜 배지로 대체(상표권 부담 없이 구분 가능) */
// logo: site/img/banks/ 에 해당 파일이 있으면 로고로 표시, 없으면 색상 뱃지로 자동 대체
const BANK_BRAND = {
  "신한은행":    { label: "신한", color: "#0046FF", logo: "shinhan.png" },
  "국민은행":    { label: "KB",  color: "#FFB300", logo: "kb.png" },
  "KB국민은행":  { label: "KB",  color: "#FFB300", logo: "kb.png" },
  "우리은행":    { label: "우리", color: "#0067AC", logo: "woori.png" },
  "하나은행":    { label: "하나", color: "#00857C", logo: "hana.png" },
  "농협은행":    { label: "NH",  color: "#00A651", logo: "nh.png" },
  "NH농협은행":  { label: "NH",  color: "#00A651", logo: "nh.png" },
  "IBK기업은행": { label: "IBK", color: "#0072BC", logo: "ibk.png" },
  "카카오뱅크":  { label: "카카오", color: "#FFCD00", logo: "kakaobank.png" },
  "토스뱅크":    { label: "토스", color: "#0064FF", logo: "tossbank.png" },
  "케이뱅크":    { label: "케이", color: "#FF5F3B", logo: "kbank.png" },
  "SC제일은행":  { label: "SC",  color: "#12A0D7", logo: "sc.png" },
  "부산은행":    { label: "부산", color: "#004EA2", logo: "busan.png" },
  "대구은행":    { label: "대구", color: "#EE7D1F", logo: "daegu.png" },
  "경남은행":    { label: "경남", color: "#009944", logo: "gyeongnam.png" },
  "광주은행":    { label: "광주", color: "#F58220", logo: "gwangju.png" },
  "전북은행":    { label: "전북", color: "#EE3524", logo: "jeonbuk.png" },
  "제주은행":    { label: "제주", color: "#00AEEF", logo: "jeju.png" },
};
const BANK_FALLBACK_COLORS = ["#5F6368", "#7B61FF", "#00838F", "#8D6E63", "#546E7A"];

function bankBadge(name) {
  const brand = BANK_BRAND[name];
  // 로고 파일이 지정된 은행: 로고 이미지 우선(없으면 onerror로 색상 뱃지 대체)
  if (brand && brand.logo) {
    return `<img class="bank-logo" src="img/banks/${brand.logo}" alt="${escapeHtml(name)}" ` +
           `onerror="bankLogoFallback(this,'${escapeHtml(name)}')">`;
  }
  if (brand) {
    return `<span class="bank-badge" style="background:${brand.color}">${escapeHtml(brand.label)}</span>`;
  }
  // 매핑에 없는 은행: 이름 기반 해시로 고정 색상 + 첫 글자
  const hash = [...(name || "?")].reduce((h, c) => h + c.charCodeAt(0), 0);
  const color = BANK_FALLBACK_COLORS[hash % BANK_FALLBACK_COLORS.length];
  const label = (name || "?").slice(0, 2);
  return `<span class="bank-badge" style="background:${color}">${escapeHtml(label)}</span>`;
}

// 로고 파일이 없거나 로드 실패 시 색상 뱃지로 대체
function bankLogoFallback(img, name) {
  const brand = BANK_BRAND[name] || {};
  const color = brand.color || "#888";
  const label = brand.label || (name || "?").slice(0, 2);
  const span = document.createElement("span");
  span.className = "bank-badge";
  span.style.background = color;
  span.textContent = label;
  img.replaceWith(span);
}

/* ── 내 계좌 (계좌조회 + 이체) ──────────────────────────────────── */
const won = (n) => Number(n).toLocaleString("ko-KR") + "원";
const ACCOUNT_TABS = ["inquiry", "transfer"];

function accountGoTab(name) {
  if (!ACCOUNT_TABS.includes(name)) name = "inquiry";
  document.querySelectorAll(".account-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.accountTab === name)
  );
  ACCOUNT_TABS.forEach((t) => {
    document.getElementById(`account-panel-${t}`).style.display = t === name ? "block" : "none";
  });
}

document.addEventListener("click", (e) => {
  const tab = e.target.closest(".account-tab");
  if (tab) accountGoTab(tab.dataset.accountTab);
});

function resetAccountUI() {
  // 로그인/계정 전환 시 이전 사용자의 거래내역·이체 진행 상태가 남지 않도록 초기화
  const detail = document.getElementById("acct-detail");
  detail.style.display = "none";
  document.getElementById("acct-transactions").innerHTML = "";
  document.getElementById("tf-to").value = "";
  document.getElementById("tf-amount").value = "";
  document.getElementById("tf-memo").value = "";
  document.getElementById("tf-sender-memo").value = "";
  document.getElementById("tf-status").textContent = "";
  document.getElementById("tf-after").textContent = "";
  tfResetVerify();
  tfGoStep(1);
}

async function loadAccounts() {
  resetAccountUI();
  try {
    const res = await apiFetch("/api/accounts");
    if (!res.ok) throw new Error("계좌 조회 실패");
    const { accounts } = await res.json();
    renderAccountCards(accounts);
    fillTransferFrom(accounts);
  } catch (err) {
    document.getElementById("account-cards").innerHTML =
      '<div class="card"><p>계좌 정보를 불러오지 못했습니다. 백엔드(:8000)가 실행 중인지 확인하세요.</p></div>';
    console.error("계좌 로드 실패:", err);
  }
}

function renderAccountCards(accounts) {
  document.getElementById("account-cards").innerHTML = accounts
    .map(
      (a) =>
        `<div class="card clickable acct-card" data-acct-id="${a.id}" data-acct-no="${a.account_no}">
           <div class="acct-bank">${bankBadge(a.bank_name)} ${escapeHtml(a.bank_name)}</div>
           <div class="acct-no">${escapeHtml(a.account_no)}</div>
           <div class="acct-balance">${won(a.balance)}</div>
         </div>`
    )
    .join("");
}

/* 받는 은행 선택 목록 */
const TF_BANKS = [
  "신한은행", "국민은행", "우리은행", "하나은행", "NH농협은행",
  "IBK기업은행", "카카오뱅크", "토스뱅크", "케이뱅크", "SC제일은행",
  "부산은행", "대구은행", "경남은행", "광주은행", "전북은행", "제주은행",
];

let tfAccounts = [];        // 내 계좌 캐시
let tfVerified = null;      // 예금주 확인된 받는 계좌 {account_no, bank_name, holder_name, fee}

function fillTransferFrom(accounts) {
  tfAccounts = accounts;
  document.getElementById("tf-from").innerHTML = accounts
    .map((a) => `<option value="${a.account_no}">${escapeHtml(a.bank_name)} ${escapeHtml(a.account_no)} · 잔액 ${won(a.balance)}</option>`)
    .join("");
  const toBank = document.getElementById("tf-to-bank");
  if (toBank && !toBank.dataset.filled) {
    toBank.innerHTML = TF_BANKS.map((b) => `<option value="${b}">${b}</option>`).join("");
    toBank.dataset.filled = "1";
  }
  updateFromBalance();
  renderTfQuickAccounts();
}

/* 자주쓰는계좌: 내 다른 계좌 + 미리 만들어둔 데모 수신 계좌 */
const TF_FAVORITE_ACCOUNTS = [
  { bank_name: "우리은행", account_no: "1002-333-444555", holder_name: "김철수" },
  { bank_name: "하나은행", account_no: "218-910111-12345", holder_name: "이영희" },
  { bank_name: "토스뱅크", account_no: "100-2345-6789", holder_name: "박민수" },
];

function renderTfQuickAccounts() {
  const list = document.getElementById("tf-quick-accounts-list");
  if (!list) return;
  const fromNo = document.getElementById("tf-from").value;
  const mine = tfAccounts
    .filter((a) => a.account_no !== fromNo)
    .map((a) => ({ bank_name: a.bank_name, account_no: a.account_no, holder_name: a.holder_name, mine: true }));
  const items = [...mine, ...TF_FAVORITE_ACCOUNTS];
  list.innerHTML = items
    .map(
      (a) => `<button type="button" class="tf-quick-acct" data-bank="${escapeHtml(a.bank_name)}" data-no="${escapeHtml(a.account_no)}">
          ${bankBadge(a.bank_name)}
          <span class="tf-quick-acct-name">${escapeHtml(a.holder_name)}</span>
          ${a.mine ? '<em class="tf-quick-acct-tag">내계좌</em>' : ""}
        </button>`
    )
    .join("");
}

function currentFromAccount() {
  const no = document.getElementById("tf-from").value;
  return tfAccounts.find((a) => a.account_no === no) || null;
}

function tfAmountValue() {
  const raw = document.getElementById("tf-amount").value.replace(/[^0-9]/g, "");
  return raw ? parseInt(raw, 10) : 0;
}

function updateFromBalance() {
  const acc = currentFromAccount();
  document.getElementById("tf-from-balance").textContent =
    acc ? `출금가능 금액 ${won(acc.balance)}` : "";
  updateAfterBalance();
}

function updateAfterBalance() {
  const acc = currentFromAccount();
  const amt = tfAmountValue();
  const el = document.getElementById("tf-after");
  if (!acc || !amt) { el.textContent = ""; return; }
  const fee = tfVerified ? tfVerified.fee : (acc.bank_name === document.getElementById("tf-to-bank").value ? 0 : 500);
  const after = acc.balance - amt - fee;
  el.className = "tf-hint" + (after < 0 ? " err" : "");
  el.textContent = after < 0
    ? `잔액 부족 (수수료 ${won(fee)} 포함 ${won(amt + fee)} 필요)`
    : `이체 후 잔액 ${won(after)}${fee ? ` (수수료 ${won(fee)} 별도)` : ""}`;
}

async function showTransactions(accountId, accountNo) {
  try {
    const res = await apiFetch(`/api/accounts/${accountId}/transactions`);
    if (!res.ok) throw new Error("거래내역 조회 실패");
    const { account, transactions } = await res.json();
    const box = document.getElementById("acct-detail");
    document.getElementById("acct-detail-title").textContent =
      `${account.bank_name} ${account.account_no} · 잔액 ${won(account.balance)}`;
    document.getElementById("acct-transactions").innerHTML = transactions.length
      ? transactions
          .map((t) => {
            const inflow = t.type === "in";
            const sign = inflow ? "+" : "−";
            const cls = inflow ? "tx-in" : "tx-out";
            const d = new Date(t.created_at * 1000).toLocaleDateString("ko-KR");
            const balText = t.balance_after == null ? "-" : won(t.balance_after);
            return `<div class="tx-row">
                <div class="tx-left">
                  <span class="tx-date">${d}</span>
                  <span class="tx-cp">${escapeHtml(t.counterparty || "-")}</span>
                </div>
                <div class="tx-right">
                  <span class="tx-amt ${cls}">${sign}${won(t.amount)}</span>
                  <span class="tx-balance">잔액 ${balText}</span>
                </div>
              </div>`;
          })
          .join("")
      : '<p class="tx-empty">거래내역이 없습니다.</p>';
    box.style.display = "block";
    // 이체 출금계좌를 클릭한 계좌로 맞춤
    document.getElementById("tf-from").value = accountNo;
  } catch (err) {
    console.error("거래내역 로드 실패:", err);
  }
}

/* 계좌 카드 클릭 → 거래내역 표시 */
document.addEventListener("click", (e) => {
  const card = e.target.closest(".acct-card");
  if (!card) return;
  showTransactions(card.dataset.acctId, card.dataset.acctNo);
});

/* ── 이체 마법사 ─────────────────────────────────────────────────── */
function tfGoStep(n) {
  [1, 2, 3].forEach((i) => {
    document.getElementById(`tf-step-${i}`).style.display = i === n ? "block" : "none";
  });
  document.querySelectorAll(".transfer-steps .step").forEach((s) => {
    s.classList.toggle("active", Number(s.dataset.step) === n);
    s.classList.toggle("done", Number(s.dataset.step) < n);
  });
}

function tfResetVerify() {
  tfVerified = null;
  document.getElementById("tf-holder").textContent = "";
  document.getElementById("tf-holder").className = "tf-holder";
}

/* 출금계좌 변경 / 금액 입력 / 받는은행 변경 → 미리보기 갱신 */
document.addEventListener("change", (e) => {
  if (e.target.id === "tf-from") { updateFromBalance(); renderTfQuickAccounts(); }
  if (e.target.id === "tf-to-bank") { tfResetVerify(); updateAfterBalance(); }
});
document.addEventListener("input", (e) => {
  if (e.target.id === "tf-amount") {
    const v = tfAmountValue();
    e.target.value = v ? v.toLocaleString("ko-KR") : "";
    updateAfterBalance();
  }
  if (e.target.id === "tf-to") tfResetVerify();
});

/* 빠른 금액 버튼 */
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".tf-quick button");
  if (!btn) return;
  const add = btn.dataset.add;
  const input = document.getElementById("tf-amount");
  if (add === "clear") { input.value = ""; }
  else if (add === "all") {
    const acc = currentFromAccount();
    const fee = tfVerified ? tfVerified.fee : 0;
    input.value = acc ? Math.max(0, acc.balance - fee).toLocaleString("ko-KR") : "";
  } else {
    input.value = (tfAmountValue() + parseInt(add, 10)).toLocaleString("ko-KR");
  }
  updateAfterBalance();
});

/* 예금주 확인 (확인 버튼 / 자주쓰는계좌 칩 공용) */
async function runTfLookup(to, from) {
  const holderEl = document.getElementById("tf-holder");
  if (!to) {
    holderEl.className = "tf-holder err";
    holderEl.textContent = "받는 분 계좌번호를 입력하세요.";
    return;
  }
  holderEl.className = "tf-holder";
  holderEl.textContent = "예금주 조회 중…";
  try {
    const res = await fetch(`/api/accounts/lookup?account_no=${encodeURIComponent(to)}&from_account=${encodeURIComponent(from)}`);
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({}));
      throw new Error(detail || "조회 실패");
    }
    const data = await res.json();
    tfVerified = data;
    // 받는 은행 자동 맞춤
    const toBank = document.getElementById("tf-to-bank");
    if ([...toBank.options].some((o) => o.value === data.bank_name)) toBank.value = data.bank_name;
    const newPayee = data.is_new_payee
      ? `<div class="tf-newpayee">⚠️ 처음 보내는 계좌입니다. 예금주명을 꼭 확인하세요.</div>` : "";
    holderEl.className = "tf-holder ok";
    holderEl.innerHTML = `✅ <b>${escapeHtml(data.holder_name)}</b> (${escapeHtml(data.bank_name)})` +
      (data.fee ? ` · 타행 수수료 ${won(data.fee)}` : " · 수수료 면제") + newPayee;
    showModal(`
      <h3>받는 분 확인</h3>
      <div class="cf-row"><span>예금주</span><b>${escapeHtml(data.holder_name)}</b></div>
      <div class="cf-row"><span>은행명</span><b>${escapeHtml(data.bank_name)}</b></div>
      <div class="cf-row"><span>계좌번호</span><b>${escapeHtml(data.account_no)}</b></div>
      <div class="cf-row"><span>수수료</span><b>${data.fee ? won(data.fee) : "면제"}</b></div>
      ${data.is_new_payee ? '<div class="tf-newpayee">⚠️ 처음 보내는 계좌입니다. 예금주명을 꼭 확인하세요.</div>' : ""}
      <button class="btn btn-primary" type="button" id="modal-confirm-ok">확인</button>`);
    updateAfterBalance();
  } catch (err) {
    tfResetVerify();
    holderEl.className = "tf-holder err";
    holderEl.textContent = "❌ " + err.message;
  }
}

document.addEventListener("click", (e) => {
  if (e.target.id !== "tf-lookup") return;
  const from = document.getElementById("tf-from").value;
  const to = document.getElementById("tf-to").value.trim();
  runTfLookup(to, from);
});

/* 자주쓰는계좌 칩 클릭 → 받는 계좌 자동입력 + 예금주 확인 */
document.addEventListener("click", (e) => {
  const chip = e.target.closest(".tf-quick-acct");
  if (!chip) return;
  const bank = chip.dataset.bank;
  const no = chip.dataset.no;
  document.getElementById("tf-to-bank").value = bank;
  document.getElementById("tf-to").value = no;
  const from = document.getElementById("tf-from").value;
  runTfLookup(no, from);
});

/* STEP1 → STEP2 (확인 화면) */
document.addEventListener("click", (e) => {
  if (e.target.id !== "tf-to-confirm") return;
  const acc = currentFromAccount();
  const amount = tfAmountValue();
  const statusEl = document.getElementById("tf-status");
  statusEl.className = "tf-status";

  if (!acc) { statusEl.className = "tf-status err"; statusEl.textContent = "출금 계좌를 선택하세요."; return; }
  if (!tfVerified) { statusEl.className = "tf-status err"; statusEl.textContent = "받는 분 예금주 확인을 먼저 해주세요."; return; }
  if (!amount) { statusEl.className = "tf-status err"; statusEl.textContent = "이체 금액을 입력하세요."; return; }
  const fee = tfVerified.fee;
  if (acc.balance < amount + fee) { statusEl.className = "tf-status err"; statusEl.textContent = "잔액이 부족합니다."; return; }

  const memo = document.getElementById("tf-memo").value.trim() || acc.holder_name;
  const senderMemo = document.getElementById("tf-sender-memo").value.trim() || tfVerified.holder_name;
  document.getElementById("tf-confirm-box").innerHTML = `
    <div class="cf-row"><span>출금 계좌</span><b>${escapeHtml(acc.bank_name)} ${escapeHtml(acc.account_no)}</b></div>
    <div class="cf-row"><span>받는 분</span><b>${escapeHtml(tfVerified.holder_name)} (${escapeHtml(tfVerified.bank_name)})</b></div>
    <div class="cf-row"><span>받는 계좌</span><b>${escapeHtml(tfVerified.account_no)}</b></div>
    <div class="cf-row"><span>받는 분 통장 표시</span><b>${escapeHtml(memo)}</b></div>
    <div class="cf-row"><span>보내는 분 통장 표시</span><b>${escapeHtml(senderMemo)}</b></div>
    <div class="cf-divider"></div>
    <div class="cf-row"><span>이체 금액</span><b>${won(amount)}</b></div>
    <div class="cf-row"><span>수수료</span><b>${fee ? won(fee) : "면제"}</b></div>
    <div class="cf-row cf-total"><span>출금 합계</span><b>${won(amount + fee)}</b></div>
    <div class="cf-row"><span>이체 후 잔액</span><b>${won(acc.balance - amount - fee)}</b></div>`;
  document.getElementById("tf-status2").textContent = "";
  tfGoStep(2);
});

/* STEP2 이전 */
document.addEventListener("click", (e) => {
  if (e.target.id === "tf-back") tfGoStep(1);
});

/* STEP2 이체 실행 → POST → 폴링 → STEP3 영수증 */
document.addEventListener("click", async (e) => {
  if (e.target.id !== "tf-submit") return;
  const acc = currentFromAccount();
  const amount = tfAmountValue();
  const memo = document.getElementById("tf-memo").value.trim();
  const senderMemo = document.getElementById("tf-sender-memo").value.trim();
  const password = document.getElementById("tf-password").value;
  const confirmed = document.getElementById("tf-confirm-check").checked;
  const statusEl = document.getElementById("tf-status2");
  statusEl.className = "tf-status";
  // 확인 체크 + 비밀번호 검사(백엔드가 최종 검증하지만 UX상 선차단)
  if (!confirmed) {
    statusEl.className = "tf-status err";
    statusEl.textContent = "받는 분과 금액을 확인한 뒤 체크해 주세요.";
    return;
  }
  if (!password) {
    statusEl.className = "tf-status err";
    statusEl.textContent = "이체 비밀번호를 입력해 주세요.";
    return;
  }
  statusEl.textContent = "이체 요청 중…";
  e.target.disabled = true;

  try {
    const res = await apiFetch("/api/transfer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        from_account: acc.account_no, to_account: tfVerified.account_no,
        amount, memo: memo || null, sender_memo: senderMemo || null,
        password,
      }),
    });
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({ detail: "이체 실패" }));
      throw new Error(detail || "이체 실패");
    }
    const { transfer_id } = await res.json();
    statusEl.textContent = "이체 처리 중…";
    await pollTransfer(transfer_id, statusEl);
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = "이체 실패: " + err.message;
  } finally {
    e.target.disabled = false;
  }
});

/* STEP3 완료 → 초기화 */
document.addEventListener("click", (e) => {
  if (e.target.id !== "tf-done") return;
  document.getElementById("tf-to").value = "";
  document.getElementById("tf-amount").value = "";
  document.getElementById("tf-memo").value = "";
  document.getElementById("tf-sender-memo").value = "";
  document.getElementById("tf-status").textContent = "";
  document.getElementById("tf-after").textContent = "";
  document.getElementById("tf-password").value = "";
  document.getElementById("tf-confirm-check").checked = false;
  tfResetVerify();
  tfGoStep(1);
});

async function pollTransfer(transferId, statusEl) {
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 500));
    const res = await fetch(`/api/transfers/${transferId}`);
    const tr = await res.json();
    if (tr.status === "completed") {
      renderReceipt(tr);
      loadAccounts(); // 잔액 갱신
      tfGoStep(3);
      return;
    }
    if (tr.status === "failed") {
      statusEl.className = "tf-status err";
      statusEl.textContent = "이체 실패: " + (tr.error || "처리 실패");
      return;
    }
  }
  statusEl.className = "tf-status";
  statusEl.textContent = "이체 처리가 지연되고 있습니다. 잠시 후 내역을 확인하세요.";
}

function renderReceipt(tr) {
  const dt = new Date(tr.created_at * 1000).toLocaleString("ko-KR");
  const txno = "T" + String(tr.id).padStart(8, "0");
  document.getElementById("tf-receipt").innerHTML = `
    <div class="rc-check">✓</div>
    <div class="rc-title">이체가 완료되었습니다</div>
    <div class="rc-amount">${won(tr.amount)}</div>
    <div class="rc-list">
      <div class="cf-row"><span>받는 분</span><b>${escapeHtml(tr.to_holder)} (${escapeHtml(tr.to_bank)})</b></div>
      <div class="cf-row"><span>받는 계좌</span><b>${escapeHtml(tr.to_account)}</b></div>
      <div class="cf-row"><span>출금 계좌</span><b>${escapeHtml(tr.from_account)}</b></div>
      <div class="cf-row"><span>수수료</span><b>${tr.fee ? won(tr.fee) : "면제"}</b></div>
      <div class="cf-row"><span>이체 일시</span><b>${dt}</b></div>
      <div class="cf-row"><span>거래번호</span><b>${txno}</b></div>
    </div>`;
}

/* ── 인기 통계 (상품안내 상단: 은행 순위 + 카테고리별 Top 5) ──────── */
async function loadProductStats() {
  loadBankRanking("stat-banks", 5, false);   // 상품안내: 조회수 숨김
  loadTopProductsCarousel("stat-products");
}

async function loadBankRanking(targetId = "stat-banks", limit = 10, showNum = true) {
  const box = document.getElementById(targetId);
  try {
    const res = await fetch(`/api/stats/banks?limit=${limit}`);
    const { banks } = await res.json();
    if (!banks.length) {
      box.innerHTML = statEmpty();
      return;
    }
    const max = Math.max(1, ...banks.map((b) => b.count));
    box.innerHTML = banks
      .map(
        (b, i) =>
          `<div class="bar-row">` +
          `<span class="rank">${i + 1}</span>` +
          bankBadge(b.name) +
          `<span class="name">${escapeHtml(b.name)}</span>` +
          `<div class="bar-track"><div class="bar-fill" style="width:${(b.count / max) * 100}%"></div></div>` +
          (showNum ? `<span class="num">${b.count}</span>` : ``) +
          `</div>`
      )
      .join("");
  } catch (err) {
    box.innerHTML = statError();
    console.error("은행 순위 로드 실패:", err);
  }
}

async function loadTopProducts(targetId = "stat-products") {
  const box = document.getElementById(targetId);
  try {
    const res = await fetch("/api/stats/top-products");
    const { categories } = await res.json();
    const names = Object.keys(categories);
    if (!names.length) {
      box.innerHTML = statEmpty();
      return;
    }
    box.innerHTML = names
      .map((cat) => {
        const items = categories[cat];
        const rows = items
          .map(
            (p, i) =>
              `<li><span class="rank">${i + 1}</span>` +
              `<span class="p-name">${escapeHtml(p.product)}</span>` +
              `<span class="p-count">${p.count}</span></li>`
          )
          .join("");
        return `<div class="topcat"><div class="topcat-title">${escapeHtml(cat)}</div><ol class="topcat-list">${rows}</ol></div>`;
      })
      .join("");
  } catch (err) {
    box.innerHTML = statError();
    console.error("인기 상품 로드 실패:", err);
  }
}

/* 상품안내 페이지 전용: 카테고리별 Top5 캐러셀(스와이프).
   옆 은행순위(5줄)와 높이를 맞추기 위해 전체를 한 번에 나열하지 않고
   한 카테고리씩 슬라이드로 보여준다. 내용은 이용통계와 동일하게 순위+상품명+조회수. */
let statProductsIndex = 0;

async function loadTopProductsCarousel(targetId = "stat-products") {
  const box = document.getElementById(targetId);
  try {
    const res = await fetch("/api/stats/top-products");
    const { categories } = await res.json();
    const names = Object.keys(categories).filter((c) => (categories[c] || []).length > 0);
    if (!names.length) {
      box.innerHTML = statEmpty();
      return;
    }
    const slides = names
      .map((cat) => {
        const rows = categories[cat]
          .map(
            (p, i) =>
              `<li><span class="rank">${i + 1}</span>` +
              `<span class="p-name">${escapeHtml(p.product)}</span></li>`
          )
          .join("");
        return `<div class="stat-products-slide"><div class="topcat"><div class="topcat-title">${escapeHtml(cat)}</div><ol class="topcat-list">${rows}</ol></div></div>`;
      })
      .join("");
    const dots = names
      .map(
        (cat, i) =>
          `<button class="stat-products-dot${i === 0 ? " active" : ""}" type="button" data-slide="${i}" aria-label="${escapeHtml(cat)}"></button>`
      )
      .join("");
    // 슬라이드가 1개뿐이면 화살표·dot 숨김
    const nav = names.length > 1
      ? `<button class="stat-products-nav prev" type="button" aria-label="이전 카테고리">‹</button>` +
        `<button class="stat-products-nav next" type="button" aria-label="다음 카테고리">›</button>`
      : "";
    box.innerHTML =
      `<div class="stat-products-carousel">` +
      `<div class="stat-products-track" id="stat-products-track">${slides}</div>` +
      nav +
      `</div>` +
      (names.length > 1 ? `<div class="stat-products-dots">${dots}</div>` : "");
    statProductsIndex = 0;
    goStatProductsSlide(0);
    setupStatProductsSwipe();
  } catch (err) {
    box.innerHTML = statError();
    console.error("인기 상품 로드 실패:", err);
  }
}

function goStatProductsSlide(i) {
  const track = document.getElementById("stat-products-track");
  const dots = document.querySelectorAll(".stat-products-dot");
  if (!track) return;
  const count = dots.length || 1;
  statProductsIndex = (i + count) % count;
  track.style.transform = `translateX(-${statProductsIndex * 100}%)`;
  dots.forEach((d, idx) => d.classList.toggle("active", idx === statProductsIndex));
}

/* 터치/마우스 드래그 스와이프. box.innerHTML 재설정 시 요소가 교체되므로
   리스너가 누적되지 않는다(옛 요소와 함께 GC). */
function setupStatProductsSwipe() {
  const carousel = document.querySelector(".stat-products-carousel");
  if (!carousel) return;
  let startX = null;
  const begin = (x) => { startX = x; };
  const end = (x) => {
    if (startX === null) return;
    const dx = x - startX;
    startX = null;
    if (Math.abs(dx) > 40) goStatProductsSlide(statProductsIndex + (dx < 0 ? 1 : -1));
  };
  carousel.addEventListener("touchstart", (e) => begin(e.touches[0].clientX), { passive: true });
  carousel.addEventListener("touchend", (e) => end(e.changedTouches[0].clientX));
  carousel.addEventListener("mousedown", (e) => begin(e.clientX));
  carousel.addEventListener("mouseup", (e) => end(e.clientX));
  carousel.addEventListener("mouseleave", () => { startX = null; });
}

document.addEventListener("click", (e) => {
  const dot = e.target.closest(".stat-products-dot");
  if (dot) { goStatProductsSlide(Number(dot.dataset.slide)); return; }
  if (e.target.closest(".stat-products-nav.prev")) { goStatProductsSlide(statProductsIndex - 1); return; }
  if (e.target.closest(".stat-products-nav.next")) { goStatProductsSlide(statProductsIndex + 1); return; }
});

function statEmpty() {
  return '<p class="stat-empty">아직 데이터가 없습니다 — 상품을 조회하거나 AI챗봇에 질문해 보세요.</p>';
}
function statError() {
  return '<p class="stat-empty">통계를 불러오지 못했습니다 (백엔드 :8000 확인).</p>';
}

/* 순수 CSS 도넛 차트 (conic-gradient). data: [{label, value, color?}] */
const DONUT_PALETTE = ["#0FA968", "#0B8457", "#2E86DE", "#8E7CC3", "#F2A93B", "#5F6368"];
const DONUT_GAP_DEG = 2;

function renderDonutChart(chartId, legendId, data, opts = {}) {
  const box = document.getElementById(chartId);
  if (!box) return;
  const total = data.reduce((s, d) => s + (d.value || 0), 0);
  if (!total) {
    box.closest(".donut-chart-wrap").innerHTML = statEmpty();
    return;
  }

  const palette = opts.palette || DONUT_PALETTE;
  let cursor = 0;
  const stops = [];
  data.forEach((d, i) => {
    const color = d.color || palette[i % palette.length];
    const share = d.value / total;
    const start = cursor * 360;
    const end = (cursor + share) * 360;
    const gapEnd = Math.max(start, end - DONUT_GAP_DEG);
    stops.push(`${color} ${start}deg ${gapEnd}deg`, `#fff ${gapEnd}deg ${end}deg`);
    cursor += share;
  });
  box.style.background = `conic-gradient(${stops.join(", ")})`;
  const valueEl = box.querySelector(".donut-hole-value");
  if (valueEl) valueEl.textContent = opts.centerValue ?? total;

  const legendBox = document.getElementById(legendId);
  if (legendBox) {
    legendBox.innerHTML = data
      .map((d, i) => {
        const color = d.color || palette[i % palette.length];
        const pct = Math.round((d.value / total) * 100);
        return `<li><span class="dot" style="background:${color}"></span>` +
          `<span class="lg-name">${escapeHtml(d.label)}</span>` +
          `<span class="lg-pct">${pct}%</span></li>`;
      })
      .join("");
  }
}

/* 추적: 서버로 이벤트 전송 (실패해도 무시). 전송 완료 후 resolve. */
function trackView(payload) {
  return fetch("/api/track/view", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).catch(() => {});
}

/* 상품 카드 클릭 → track/view(category) → FSS 상품 목록 표시. 카테고리별 인기상품 순위는
   실제 상품 클릭(아래 product-list-row 확장) 기준으로 집계하므로 여기선 product를 보내지 않는다. */
document.addEventListener("click", async (e) => {
  const card = e.target.closest(".prod-card");
  if (!card) return;
  loadProductList(card.dataset.category);
  await trackView({ category: card.dataset.category });
  loadProductStats();
});

/* FSS(금융감독원) 상품 목록 조회/렌더 */
async function loadProductList(category) {
  const panel = document.getElementById("product-list-panel");
  const titleEl = document.getElementById("product-list-title");
  const listEl = document.getElementById("product-list");
  panel.style.display = "block";
  titleEl.textContent = `${category} 상품 목록`;
  listEl.innerHTML = '<p class="product-list-empty">불러오는 중…</p>';
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    const res = await fetch(`/api/products?category=${encodeURIComponent(category)}`);
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({}));
      throw new Error(detail || "상품 목록을 불러오지 못했습니다.");
    }
    const { products } = await res.json();
    listEl.innerHTML = products.length
      ? products.map(renderProductRow).join("")
      : '<p class="product-list-empty">조회된 상품이 없습니다.</p>';
  } catch (err) {
    listEl.innerHTML = `<p class="product-list-empty">${escapeHtml(err.message)}</p>`;
  }
}

function renderProductRow(p) {
  const rates = p.options
    .map((o) => (o.max_rate != null ? o.max_rate : o.base_rate))
    .filter((r) => r != null);
  const minRate = rates.length ? Math.min(...rates) : null;
  const maxRate = rates.length ? Math.max(...rates) : null;
  const rateText =
    minRate == null
      ? "-"
      : minRate === maxRate
      ? `<span class="pl-rate-label">연</span><span class="pl-rate-max">${maxRate}%</span>`
      : `<span class="pl-rate-label">연</span><span class="pl-rate-min">${minRate}%</span><span class="pl-rate-sep">~</span><span class="pl-rate-max">${maxRate}%</span>`;
  const termRates = p.options
    .map(
      (o) =>
        `<span class="pl-term">${o.term_months}개월 ${o.max_rate ?? o.base_rate ?? "-"}%${
          o.save_type ? ` · ${escapeHtml(o.save_type)}` : ""
        }</span>`
    )
    .join("");
  const catBadge = p.category ? `<span class="pl-cat">${escapeHtml(p.category)}</span>` : "";
  const denyBadge = p.join_deny_label
    ? `<span class="pl-badge${p.join_deny_label === "가입제한 없음" ? " ok" : ""}">${escapeHtml(p.join_deny_label)}</span>`
    : "";
  const linkBtn = p.url
    ? `<a class="btn btn-ghost pl-link" href="${p.url}" target="_blank" rel="noopener">공식 상품 페이지 ↗</a>`
    : "";
  const detailRows = [
    ["가입방법", p.join_way],
    ["가입대상", p.join_member],
    ["우대조건", p.spcl_cnd && p.spcl_cnd !== "해당사항 없음" ? p.spcl_cnd : ""],
    ["기타 유의사항", p.etc_note],
    ["만기 후 이자율", p.mtrt_int],
  ].filter(([, v]) => v);
  const detailBody = detailRows.length
    ? detailRows
        .map(
          ([label, value]) =>
            `<div class="pl-detail-row"><span class="pl-detail-label">${escapeHtml(label)}</span><span class="pl-detail-value">${escapeHtml(value)}</span></div>`
        )
        .join("")
    : `<p class="product-list-empty">추가 상세 정보가 없습니다.</p>`;
  const dclsMeta = p.dcls_date ? `<div class="pl-detail-meta">공시 기준일: ${escapeHtml(p.dcls_date)}</div>` : "";

  return `<div class="product-list-row" data-product="${escapeHtml(p.product_name)}" data-category="${escapeHtml(p.category)}">
      <div class="pl-head">
        ${catBadge}
        <span class="pl-bank">${bankBadge(p.bank)} ${escapeHtml(p.bank)}</span>
        <span class="pl-name">${escapeHtml(p.product_name)}</span>
        ${denyBadge}
        <span class="pl-rate">${rateText}</span>
        <span class="chev">▾</span>
      </div>
      <div class="pl-terms">${termRates}</div>
      <div class="pl-detail">
        ${dclsMeta}
        ${detailBody}
        ${linkBtn}
      </div>
    </div>`;
}

/* 상품 목록 행 클릭 → 상세 정보 아코디언 확장/축소. 처음 펼칠 때만 조회(클릭)로 집계 →
   카테고리별 인기상품 TOP5는 이 조회수를 기준으로 순위가 매겨진다. */
document.addEventListener("click", async (e) => {
  if (e.target.closest(".pl-link")) return;
  const row = e.target.closest("#product-list .product-list-row");
  if (!row) return;
  const opening = !row.classList.contains("open");
  row.classList.toggle("open");
  if (opening) {
    await trackView({ product: row.dataset.product, category: row.dataset.category });
    loadProductStats();
  }
});

/* ── 은행 바로가기 ───────────────────────────────────────────────── */
let banksLoaded = false;
async function ensureBanksLoaded() {
  if (banksLoaded) return;
  banksLoaded = true;
  try {
    const res = await fetch("data/banks.json");
    const banks = await res.json();
    // 중복 URL(별칭) 제거
    const seen = new Set();
    const unique = banks.filter((b) => {
      if (seen.has(b.url)) return false;
      seen.add(b.url);
      return true;
    });
    document.getElementById("bank-grid").innerHTML = unique
      .map(
        (b) =>
          `<a class="bank-chip" href="${b.url}" target="_blank" rel="noopener" data-bank="${escapeHtml(b.name)}">` +
          `<span class="bank-chip-name">${bankBadge(b.name)} ${escapeHtml(b.name)}</span><span class="arrow">↗</span></a>`
      )
      .join("");
  } catch (err) {
    banksLoaded = false;
    console.error("banks.json 로드 실패:", err);
  }
}

/* 은행 바로가기 클릭 → track/view(bank) 후 이동 (새 탭이라 이동은 그대로) */
document.addEventListener("click", async (e) => {
  const chip = e.target.closest(".bank-chip");
  if (!chip) return;
  // 새 탭 이동은 브라우저가 처리(target=_blank), 현재 탭에서 추적·갱신
  await trackView({ bank: chip.dataset.bank });
  loadBankRanking("stat-banks", 5);
});

/* ── 인증: 로그인 / 회원가입 / 로그아웃 ─────────────────────────────── */

/* 회원가입 약관 (데모용 표준 문구) */
const TERMS_TEXT = {
  terms: {
    title: "이용약관",
    body:
      "제1조(목적)\n본 약관은 매치뱅크(이하 '회사')가 제공하는 금융상품 정보 조회 및 AI 상담 서비스(이하 '서비스')의 이용 조건을 규정합니다.\n\n" +
      "제2조(서비스의 내용)\n① 회사는 금융감독원 공시 데이터를 기반으로 예금·적금·대출 상품 정보를 제공합니다.\n② 제공되는 정보는 참고용이며, 실제 가입 조건은 각 금융기관에 확인해야 합니다.\n\n" +
      "제3조(회원의 의무)\n회원은 본인의 계정 정보를 안전하게 관리하며, 타인에게 양도·대여할 수 없습니다.\n\n" +
      "제4조(면책)\n본 서비스는 데모 목적으로 제공되며, 제공된 정보로 인한 투자·금융 결정의 책임은 회원 본인에게 있습니다.",
  },
  privacy: {
    title: "개인정보 수집·이용 동의",
    body:
      "1. 수집 항목\n- 필수: 아이디, 비밀번호, 이름\n\n" +
      "2. 수집·이용 목적\n- 회원 식별 및 로그인, 서비스 제공, 이용 내역 관리\n\n" +
      "3. 보유 및 이용 기간\n- 회원 탈퇴 시까지 보관하며, 관련 법령에 따라 필요한 경우 해당 기간 동안 보관합니다.\n\n" +
      "4. 동의 거부 권리\n- 필수 항목 수집에 동의하지 않을 수 있으나, 이 경우 회원가입이 제한됩니다.\n\n" +
      "※ 본 서비스는 데모 목적이며 실제 개인정보를 수집·저장하지 않습니다.",
  },
  openbanking: {
    title: "오픈뱅킹 이용 동의",
    body:
      "1. 목적\n등록하신 계좌의 잔액 조회 및 출금이체(이체) 서비스를 제공하기 위해 오픈뱅킹 서비스를 이용합니다.\n\n" +
      "2. 제공 정보\n- 계좌번호, 예금주명, 잔액, 거래내역\n\n" +
      "3. 출금이체 동의\n회원이 요청한 이체 건에 한하여 등록 계좌에서 출금이 이루어집니다.\n\n" +
      "4. 철회\n오픈뱅킹 이용 동의는 계좌 해지 또는 회원 탈퇴 시 철회됩니다.\n\n" +
      "※ 본 서비스는 데모 목적이며 실제 출금·조회가 발생하지 않습니다.",
  },
};

function showTermsModal(kind) {
  const t = TERMS_TEXT[kind];
  if (!t) return;
  showModal(
    `<h3 style="margin:0 0 12px">${escapeHtml(t.title)}</h3>` +
    `<div class="terms-body">${escapeHtml(t.body)}</div>` +
    `<button class="btn btn-primary" id="modal-confirm-ok" style="margin-top:16px;width:100%">확인</button>`
  );
}

/* 회원가입 인증 상태(데모 시뮬레이션) */
let signupPhoneVerified = false;
let signupPhoneExpected = "";     // 고정 코드
let signupAccountVerified = false;
let signupAccountExpected = "";   // 1원 인증 난수 코드

function fillSignupBanks() {
  const sel = document.getElementById("signup-bank");
  if (!sel || sel.options.length) return;
  sel.innerHTML =
    `<option value="" disabled selected>은행을 선택하세요</option>` +
    TF_BANKS.map((b) => `<option value="${escapeHtml(b)}">${escapeHtml(b)}</option>`).join("");
}

function updateSignupButtonState() {
  const btn = document.getElementById("signup-submit");
  if (!btn) return;
  const terms = document.getElementById("signup-agree-terms").checked;
  const privacy = document.getElementById("signup-agree-privacy").checked;
  const openbanking = document.getElementById("signup-agree-openbanking").checked;
  btn.disabled = !(terms && privacy && openbanking && signupPhoneVerified && signupAccountVerified);
}

/* 폼 리셋 시 인증 상태·안내 초기화 */
function resetSignupVerification() {
  signupPhoneVerified = false; signupPhoneExpected = "";
  signupAccountVerified = false; signupAccountExpected = "";
  const hide = (id) => { const el = document.getElementById(id); if (el) el.style.display = "none"; };
  const clear = (id) => { const el = document.getElementById(id); if (el) el.textContent = ""; };
  hide("signup-phone-code-row"); hide("signup-account-code-row");
  clear("signup-phone-hint"); clear("signup-account-hint");
}

document.addEventListener("click", (e) => {
  const view = e.target.closest(".auth-agree-view");
  if (view) { e.preventDefault(); showTermsModal(view.dataset.terms); return; }

  // 휴대폰 인증번호 받기 (고정 코드 시뮬레이션)
  if (e.target.id === "signup-phone-send") {
    const phone = document.getElementById("signup-phone").value.replace(/[^0-9]/g, "");
    const hint = document.getElementById("signup-phone-hint");
    if (phone.length < 10) { hint.className = "tf-status err"; hint.textContent = "전화번호를 정확히 입력하세요."; return; }
    signupPhoneExpected = "123456";
    document.getElementById("signup-phone-code-row").style.display = "";
    hint.className = "tf-hint";
    hint.textContent = "인증번호 123456 이(가) 발송되었습니다. (데모)";
    return;
  }
  if (e.target.id === "signup-phone-verify") {
    const code = document.getElementById("signup-phone-code").value.trim();
    const hint = document.getElementById("signup-phone-hint");
    if (signupPhoneExpected && code === signupPhoneExpected) {
      signupPhoneVerified = true;
      hint.className = "tf-hint"; hint.innerHTML = `<span class="verify-badge ok">✓ 휴대폰 인증 완료</span>`;
    } else {
      signupPhoneVerified = false;
      hint.className = "tf-status err"; hint.textContent = "인증번호가 일치하지 않습니다.";
    }
    updateSignupButtonState();
    return;
  }

  // 계좌 1원 인증 요청 (난수 코드 시뮬레이션)
  if (e.target.id === "signup-account-verify") {
    const bank = document.getElementById("signup-bank").value;
    const acctNo = document.getElementById("signup-account-no").value.trim();
    const holder = document.getElementById("signup-account-holder").value.trim();
    const name = document.getElementById("signup-name").value.trim();
    const hint = document.getElementById("signup-account-hint");
    if (!bank || !acctNo) { hint.className = "tf-status err"; hint.textContent = "은행과 계좌번호를 입력하세요."; return; }
    if (holder && name && holder !== name) {
      hint.className = "tf-status err"; hint.textContent = "예금주명이 가입자 이름과 다릅니다."; return;
    }
    signupAccountExpected = String(Math.floor(100 + Math.random() * 900)); // 3자리
    document.getElementById("signup-account-code-row").style.display = "";
    hint.className = "tf-hint";
    hint.textContent = `입금자명 '매치[${signupAccountExpected}]'으로 1원이 입금되었습니다. 괄호 안 3자리 코드를 입력하세요. (데모)`;
    return;
  }
  if (e.target.id === "signup-account-code-confirm") {
    const code = document.getElementById("signup-account-code").value.trim();
    const hint = document.getElementById("signup-account-hint");
    if (signupAccountExpected && code === signupAccountExpected) {
      signupAccountVerified = true;
      hint.className = "tf-hint"; hint.innerHTML = `<span class="verify-badge ok">✓ 계좌 인증 완료</span>`;
    } else {
      signupAccountVerified = false;
      hint.className = "tf-status err"; hint.textContent = "인증코드가 일치하지 않습니다.";
    }
    updateSignupButtonState();
    return;
  }
});

document.addEventListener("change", (e) => {
  const id = e.target.id;
  if (id === "signup-agree-terms" || id === "signup-agree-privacy" || id === "signup-agree-openbanking") {
    updateSignupButtonState();
  }
});

/* 전화번호/계좌 정보가 바뀌면 재인증을 요구(인증 상태 리셋) */
document.addEventListener("input", (e) => {
  const id = e.target.id;
  if (id === "signup-phone") {
    signupPhoneVerified = false;
    document.getElementById("signup-phone-code-row").style.display = "none";
    document.getElementById("signup-phone-hint").textContent = "";
    updateSignupButtonState();
  }
  if (id === "signup-account-no" || id === "signup-account-holder") {
    signupAccountVerified = false;
    document.getElementById("signup-account-code-row").style.display = "none";
    document.getElementById("signup-account-hint").textContent = "";
    updateSignupButtonState();
  }
});

document.addEventListener("submit", (e) => {
  if (e.target.id === "login-form") { e.preventDefault(); handleLogin(); }
  if (e.target.id === "signup-form") { e.preventDefault(); handleSignup(); }
});

async function handleLogin() {
  const username = document.getElementById("login-username").value.trim();
  const password = document.getElementById("login-password").value;
  const statusEl = document.getElementById("login-status");
  statusEl.className = "tf-status";
  statusEl.textContent = "로그인 중…";
  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({}));
      throw new Error(detail || "로그인에 실패했습니다.");
    }
    const data = await res.json();
    setAuth(data);
    statusEl.textContent = "";
    document.getElementById("login-form").reset();
    refreshAuthUI();
    navigate("home");
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
}

async function handleSignup() {
  const username = document.getElementById("signup-username").value.trim();
  const name = document.getElementById("signup-name").value.trim();
  const password = document.getElementById("signup-password").value;
  const password2 = document.getElementById("signup-password2").value;
  const statusEl = document.getElementById("signup-status");
  statusEl.className = "tf-status";
  if (password !== password2) {
    statusEl.className = "tf-status err";
    statusEl.textContent = "비밀번호가 일치하지 않습니다.";
    return;
  }
  if (!document.getElementById("signup-agree-terms").checked ||
      !document.getElementById("signup-agree-privacy").checked ||
      !document.getElementById("signup-agree-openbanking").checked) {
    statusEl.className = "tf-status err";
    statusEl.textContent = "필수 약관에 동의해 주세요.";
    return;
  }
  if (!signupPhoneVerified || !signupAccountVerified) {
    statusEl.className = "tf-status err";
    statusEl.textContent = "휴대폰·계좌 인증을 완료해 주세요.";
    return;
  }
  const payload = {
    username, password, name,
    phone: document.getElementById("signup-phone").value.replace(/[^0-9]/g, ""),
    email: document.getElementById("signup-email").value.trim(),
    bank_name: document.getElementById("signup-bank").value,
    account_no: document.getElementById("signup-account-no").value.trim(),
    account_holder: document.getElementById("signup-account-holder").value.trim(),
    nickname: document.getElementById("signup-account-nickname").value.trim(),
    is_primary: document.getElementById("signup-account-primary").checked,
    agree_openbanking: document.getElementById("signup-agree-openbanking").checked,
  };
  statusEl.textContent = "가입 처리 중…";
  try {
    const res = await fetch("/api/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({}));
      throw new Error(detail || "회원가입에 실패했습니다.");
    }
    const data = await res.json();
    setAuth(data);
    statusEl.textContent = "";
    document.getElementById("signup-form").reset();
    resetSignupVerification();
    updateSignupButtonState();
    refreshAuthUI();
    navigate("home");
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
}

document.addEventListener("click", (e) => {
  if (!e.target.closest("#nav-logout")) return;
  e.preventDefault();
  clearAuth();
  refreshAuthUI();
  navigate("home");
});

/* ── Backoffice (관리자 전용) ───────────────────────────────────────── */
const BO_TABS = ["dashboard", "members", "transfers", "usage", "perf", "products", "faq", "settings"];
const boLoaded = {};   // 탭별 최초 로드 여부(지연 로드)
let boUserOffset = 0;
let boUserQuery = "";
let boTransferOffset = 0;
let boTransferStatus = "";
const BO_PAGE_SIZE = 20;

function boGoTab(name) {
  if (!BO_TABS.includes(name)) name = "dashboard";
  document.querySelectorAll(".bo-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.boTab === name)
  );
  BO_TABS.forEach((t) => {
    document.getElementById(`bo-panel-${t}`).style.display = t === name ? "block" : "none";
  });
  ensureBoTabLoaded(name);
  setBoAutoRefresh(name);
}

// 대시보드/이체모니터링에서만 주기적 자동 갱신(10초). 그 외 탭·섹션에선 해제.
const BO_REFRESH_MS = 10000;
let boRefreshTimer = null;

function stopBoAutoRefresh() {
  if (boRefreshTimer) { clearInterval(boRefreshTimer); boRefreshTimer = null; }
}

function setBoAutoRefresh(name) {
  stopBoAutoRefresh();
  if (name === "dashboard") {
    boRefreshTimer = setInterval(loadBoDashboard, BO_REFRESH_MS);
  } else if (name === "transfers") {
    boRefreshTimer = setInterval(refreshBoTransfersLive, BO_REFRESH_MS);
  }
}

// 이체모니터링 라이브 갱신: 페이지네이션된 본 목록은 건드리지 않고
// 요약·예약 큐·보안 이벤트(실시간성 높은 부분)만 새로고침.
async function refreshBoTransfersLive() {
  loadBoScheduledQueue();
  loadBoSecurityEvents();
  try {
    const res = await apiFetch(`/api/admin/transfers?offset=0&limit=1&status=${boTransferStatus}`);
    if (res.ok) renderBoTransferSummary((await res.json()).summary);
  } catch (err) {
    console.error("이체 요약 갱신 실패:", err);
  }
}

function ensureBoTabLoaded(name) {
  if (boLoaded[name]) return;
  boLoaded[name] = true;
  if (name === "dashboard") loadBoDashboard();
  if (name === "members") loadBoUsers();
  if (name === "transfers") loadBoTransfers();
  if (name === "usage") {
    loadBoUsageStats();
    loadBankRanking("bo-stat-banks");
    loadTopProducts("bo-stat-products");
  }
  if (name === "perf") { loadBoHealth(); loadBoBatchPerf(); loadBoChatbotConfig(); loadBoTransferPolicy(); }
  if (name === "products") {
    loadBankRanking("bo-product-stat-banks");
    loadTopProducts("bo-product-stat-products");
    loadBoDocuments();
  }
  if (name === "faq") { loadBoNotices(); loadBoFaqs(); }
  if (name === "settings") { loadBoSystemOverview(); loadBoInfraConfig(); }
}

document.addEventListener("click", (e) => {
  const tab = e.target.closest(".bo-tab");
  if (tab) boGoTab(tab.dataset.boTab);
});

/* 대시보드: KPI 요약 + 이용 추이 + 은행별 비중/이체 상태 도넛 */
async function loadBoDashboard() {
  try {
    const [usersRes, transfersRes, usageRes, healthRes] = await Promise.all([
      apiFetch("/api/admin/users?limit=1"),
      apiFetch("/api/admin/transfers?limit=1"),
      apiFetch("/api/admin/usage-stats"),
      apiFetch("/api/admin/health"),
    ]);
    const users = await usersRes.json();
    const transfers = await transfersRes.json();
    const usage = await usageRes.json();
    const health = await healthRes.json();

    renderBoDashboardSummary(users, transfers, usage, health);
    renderBoUsageDaily(usage.daily, "bo-dash-usage-daily");
    renderBoDashboardStatusDonut(transfers.summary);
  } catch (err) {
    document.getElementById("bo-dash-summary").innerHTML = statError();
    console.error("대시보드 로드 실패:", err);
  }
  loadBoDashboardBankDonut();
  loadBoDashboardInfra();
}

const INFRA_STATUS_LABEL = { ok: "정상", warn: "주의", down: "연결안됨" };

/* 대시보드: 인프라 연동 현황(Kafka/ES/Phoenix) + LLM·RAG 24h 사용 지표 */
async function loadBoDashboardInfra() {
  const statusBox = document.getElementById("bo-dash-infra-status");
  const metricBox = document.getElementById("bo-dash-infra-metrics");
  const cfgBox = document.getElementById("bo-dash-infra-config");
  statusBox.textContent = "불러오는 중…";
  try {
    const res = await apiFetch("/api/admin/infra-metrics");
    if (!res.ok) throw new Error("인프라 지표 조회 실패");
    const data = await res.json();

    const cards = [
      { name: "Kafka", ...data.kafka },
      { name: "Elasticsearch", ...data.elasticsearch },
      { name: "Phoenix (LLM 추적)", ...data.phoenix },
      { name: "예약 이체 폴러", ...data.scheduled_poller },
    ];
    statusBox.innerHTML = cards
      .map(
        (c) =>
          `<div class="status-card"><div class="status-card-head"><b>${escapeHtml(c.name)}</b>` +
          `<span class="status-badge ${c.status}">${INFRA_STATUS_LABEL[c.status] || c.status}</span></div>` +
          `<p>${escapeHtml(c.detail)}</p></div>`
      )
      .join("");

    const p = data.phoenix;
    metricBox.innerHTML = `
      <div class="metric"><div class="value">${p.llm_avg_latency_ms}ms</div><div class="label">LLM 평균 응답 지연</div></div>
      <div class="metric"><div class="value">${p.llm_request_count}</div><div class="label">LLM 요청 수</div></div>
      <div class="metric"><div class="value">${p.rag_search_count}</div><div class="label">RAG 검색 횟수</div></div>
      <div class="metric"><div class="value">${p.cache_hit_rate}%</div><div class="label">캐시 히트율</div></div>`;

    const lc = data.llm_config, rc = data.rag_config;
    cfgBox.textContent =
      `현재 설정: ${lc.provider || "-"} · ${lc.model || "-"} · ` +
      `RAG top_k=${rc.top_k} · 캐시 ${rc.cache_enabled ? "사용중" : "미사용"}`;
  } catch (err) {
    statusBox.innerHTML = statError();
    console.error("인프라 지표 로드 실패:", err);
  }
}

function renderBoDashboardSummary(users, transfers, usage, health) {
  const daily = usage.daily || [];
  // daily는 이벤트가 있었던 날짜만 포함(공백 스킵)되므로, 엄밀한 캘린더상 "어제"가 아니라
  // "직전 데이터 존재일" 대비 증감이다. 데모 데이터셋 특성상 이 정도 근사로 충분하다.
  const today = daily.length ? daily[daily.length - 1].count : 0;
  const prev = daily.length > 1 ? daily[daily.length - 2].count : 0;
  const trend = usageTrendBadge(today, prev);

  const checks = health.checks || [];
  const okCount = checks.filter((c) => c.status === "ok").length;
  const healthCls = okCount === checks.length && checks.length > 0 ? "ok" : okCount === 0 ? "down" : "warn";

  document.getElementById("bo-dash-summary").innerHTML = `
    <div class="metric"><div class="value">${users.total}</div><div class="label">총 회원수</div></div>
    <div class="metric"><div class="value">${won(users.total_balance)}</div><div class="label">총 예치금</div></div>
    <div class="metric"><div class="value">${transfers.summary.total}</div><div class="label">총 이체 건수</div></div>
    <div class="metric"><div class="value">${won(transfers.summary.completed_amount || 0)}</div><div class="label">이체 완료금액</div></div>
    <div class="metric"><div class="value">${today}${trend}</div><div class="label">오늘 이용 이벤트</div></div>
    <div class="metric"><div class="value"><span class="status-badge ${healthCls}">${okCount}/${checks.length} 정상</span></div><div class="label">시스템 상태</div></div>`;
}

function usageTrendBadge(today, prev) {
  if (prev === 0) {
    return today > 0
      ? `<span class="trend-badge up">▲ 신규</span>`
      : `<span class="trend-badge flat">− 0%</span>`;
  }
  const pct = Math.round(((today - prev) / prev) * 100);
  if (pct > 0) return `<span class="trend-badge up">▲ ${pct}%</span>`;
  if (pct < 0) return `<span class="trend-badge down">▼ ${Math.abs(pct)}%</span>`;
  return `<span class="trend-badge flat">− 0%</span>`;
}

function renderBoDashboardStatusDonut(summary) {
  const data = [
    { label: "완료", value: summary.completed, color: "#0FA968" },
    { label: "대기", value: summary.pending, color: "#F2A93B" },
    { label: "실패", value: summary.failed, color: "#C5221F" },
  ];
  renderDonutChart("bo-dash-donut-transfers", "bo-dash-donut-transfers-legend", data, { centerValue: summary.total });
}

async function loadBoDashboardBankDonut() {
  try {
    const res = await fetch("/api/stats/banks?limit=20");
    const { banks } = await res.json();
    if (!banks.length) {
      document.getElementById("bo-dash-donut-banks").closest(".donut-chart-wrap").innerHTML = statEmpty();
      return;
    }
    const top = banks.slice(0, 5);
    const restTotal = banks.slice(5).reduce((s, b) => s + b.count, 0);
    const data = top.map((b) => ({ label: b.name, value: b.count }));
    if (restTotal > 0) data.push({ label: "기타", value: restTotal, color: "#5F6368" });
    renderDonutChart("bo-dash-donut-banks", "bo-dash-donut-banks-legend", data);
  } catch (err) {
    console.error("은행 비중 로드 실패:", err);
  }
}

/* 테스트 계정(데모 계정) 정보 표시 */
document.addEventListener("click", async (e) => {
  if (!e.target.closest("#bo-show-demo")) return;
  const box = document.getElementById("bo-demo-info");
  box.textContent = "불러오는 중…";
  try {
    const res = await apiFetch("/api/admin/demo-account");
    if (!res.ok) throw new Error("조회 실패");
    const data = await res.json();
    box.innerHTML =
      `<div class="cf-row"><span>아이디</span><b>${escapeHtml(data.username)}</b></div>` +
      `<div class="cf-row"><span>비밀번호</span><b>${escapeHtml(data.password)}</b></div>` +
      `<p class="tf-hint">${escapeHtml(data.note)}</p>`;
  } catch (err) {
    box.textContent = "불러오지 못했습니다.";
  }
});

/* 회원관리 */
async function loadBoUsers(reset = true) {
  if (reset) boUserOffset = 0;
  try {
    const res = await apiFetch(
      `/api/admin/users?offset=${boUserOffset}&limit=${BO_PAGE_SIZE}&q=${encodeURIComponent(boUserQuery)}`
    );
    if (!res.ok) throw new Error("회원 목록 조회 실패");
    const data = await res.json();
    renderBoUserSummary(data);
    renderBoUserRows(data.users, reset);
    document.getElementById("bo-user-more").style.display =
      boUserOffset + data.users.length < data.total ? "" : "none";
    boUserOffset += data.users.length;
  } catch (err) {
    console.error("회원 목록 로드 실패:", err);
  }
}

function renderBoUserSummary(data) {
  document.getElementById("bo-user-summary").innerHTML = `
    <div class="metric"><div class="value">${data.total}</div><div class="label">총 회원수</div></div>
    <div class="metric"><div class="value">${data.account_count}</div><div class="label">총 계좌수</div></div>
    <div class="metric"><div class="value">${won(data.total_balance)}</div><div class="label">총 예치금</div></div>`;
}

function renderBoUserRows(users, reset) {
  const rows = users
    .map((u) => {
      const d = new Date(u.created_at * 1000).toLocaleDateString("ko-KR");
      return `<tr><td>${escapeHtml(u.username)}</td><td>${escapeHtml(u.name)}</td>` +
        `<td>${escapeHtml(u.role)}</td><td>${d}</td></tr>`;
    })
    .join("");
  const tbody = document.getElementById("bo-user-rows");
  tbody.innerHTML = reset ? rows : tbody.innerHTML + rows;
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#bo-user-search-btn")) {
    boUserQuery = document.getElementById("bo-user-search").value.trim();
    loadBoUsers(true);
  }
  if (e.target.closest("#bo-user-more")) loadBoUsers(false);
});

/* 이체모니터링 */
async function loadBoTransfers(reset = true) {
  if (reset) boTransferOffset = 0;
  try {
    const res = await apiFetch(
      `/api/admin/transfers?offset=${boTransferOffset}&limit=${BO_PAGE_SIZE}&status=${boTransferStatus}`
    );
    if (!res.ok) throw new Error("이체 내역 조회 실패");
    const data = await res.json();
    renderBoTransferSummary(data.summary);
    renderBoTransferRows(data.transfers, reset);
    document.getElementById("bo-transfer-more").style.display =
      boTransferOffset + data.transfers.length < data.total ? "" : "none";
    boTransferOffset += data.transfers.length;
    if (reset) { loadBoScheduledQueue(); loadBoSecurityEvents(); }
  } catch (err) {
    console.error("이체 내역 로드 실패:", err);
  }
}

function renderBoTransferSummary(s) {
  document.getElementById("bo-transfer-summary").innerHTML = `
    <div class="metric"><div class="value">${s.total}</div><div class="label">총 이체</div></div>
    <div class="metric"><div class="value">${s.completed}</div><div class="label">완료</div></div>
    <div class="metric"><div class="value">${s.pending}</div><div class="label">대기</div></div>
    <div class="metric"><div class="value">${s.failed}</div><div class="label">실패</div></div>
    <div class="metric"><div class="value" style="color:#1A56DB">${s.scheduled || 0}</div><div class="label">예약</div></div>
    <div class="metric"><div class="value" style="color:#6D28D9">${s.delayed || 0}</div><div class="label">지연</div></div>
    <div class="metric"><div class="value" style="color:#5F6368">${s.canceled || 0}</div><div class="label">취소</div></div>
    <div class="metric"><div class="value">${won(s.completed_amount)}</div><div class="label">완료 금액</div></div>`;
}

// 예약/지연 대기 큐 렌더 (예정 시각 오름차순)
async function loadBoScheduledQueue() {
  const box = document.getElementById("bo-scheduled-queue");
  if (!box) return;
  try {
    const res = await apiFetch("/api/admin/scheduled-transfers");
    if (!res.ok) throw new Error("예약 큐 조회 실패");
    const { transfers } = await res.json();
    if (!transfers.length) {
      box.innerHTML = `<p class="tf-hint">대기 중인 예약/지연 이체가 없습니다.</p>`;
      return;
    }
    const now = Date.now() / 1000;
    box.innerHTML = transfers
      .map((t) => {
        const when = new Date(t.scheduled_at * 1000).toLocaleString("ko-KR",
          { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
        const remain = t.scheduled_at - now;
        const remainTxt = remain <= 0 ? "실행 임박"
          : remain < 3600 ? `${Math.ceil(remain / 60)}분 후`
          : `${Math.floor(remain / 3600)}시간 ${Math.ceil((remain % 3600) / 60)}분 후`;
        const label = BO_STATUS_LABEL[t.status] || t.status;
        return `<div class="sched-row">
          <span class="tx-status tx-${escapeHtml(t.status)}">${label}</span>
          <span class="sched-when">${when} · ${remainTxt}</span>
          <span class="sched-info">${escapeHtml(t.to_bank || "")} ${escapeHtml(t.to_account)} · ${won(t.amount)}</span>
          <button class="bo-cancel-btn" type="button" data-tf-id="${t.id}">취소</button>
        </div>`;
      })
      .join("");
  } catch (err) {
    box.innerHTML = statError();
    console.error("예약 큐 로드 실패:", err);
  }
}

const SEC_EVENT_LABEL = {
  password_fail: "비밀번호 실패",
  limit_once: "1회 한도 초과",
  limit_daily: "1일 한도 초과",
  new_payee: "신규 수취계좌",
};

// 이체 보안 이벤트 목록 + 요약
async function loadBoSecurityEvents() {
  const box = document.getElementById("bo-security-events");
  const sumBox = document.getElementById("bo-security-summary");
  if (!box) return;
  try {
    const res = await apiFetch("/api/admin/security-events?limit=30");
    if (!res.ok) throw new Error("보안 이벤트 조회 실패");
    const { events, summary } = await res.json();

    const bt = summary.by_type || {};
    sumBox.innerHTML = `
      <div class="metric"><div class="value">${summary.last_24h || 0}</div><div class="label">최근 24시간</div></div>
      <div class="metric"><div class="value" style="color:#C5221F">${bt.password_fail || 0}</div><div class="label">비밀번호 실패</div></div>
      <div class="metric"><div class="value" style="color:#92400E">${(bt.limit_once || 0) + (bt.limit_daily || 0)}</div><div class="label">한도 초과</div></div>
      <div class="metric"><div class="value" style="color:#1A56DB">${bt.new_payee || 0}</div><div class="label">신규 수취계좌</div></div>`;

    if (!events.length) {
      box.innerHTML = `<p class="tf-hint">기록된 보안 이벤트가 없습니다.</p>`;
      return;
    }
    box.innerHTML = events
      .map((e) => {
        const d = new Date(e.created_at * 1000).toLocaleString("ko-KR");
        const label = SEC_EVENT_LABEL[e.event_type] || escapeHtml(e.event_type);
        return `<div class="sec-row">
          <span class="sec-badge sec-${escapeHtml(e.event_type)}">${label}</span>
          <span class="sec-user">${escapeHtml(e.username || "-")}</span>
          <span class="sec-info">${escapeHtml(e.to_account || "")} · ${won(e.amount)}</span>
          <span class="sec-when">${d}</span>
        </div>`;
      })
      .join("");
  } catch (err) {
    box.innerHTML = statError();
    console.error("보안 이벤트 로드 실패:", err);
  }
}

const BO_STATUS_LABEL = { completed: "완료", pending: "대기", failed: "실패",
  scheduled: "예약", delayed: "지연", canceled: "취소" };

function renderBoTransferRows(transfers, reset) {
  const rows = transfers
    .map((t) => {
      const d = new Date(t.created_at * 1000).toLocaleString("ko-KR");
      let label = BO_STATUS_LABEL[t.status] || escapeHtml(t.status);
      // 예약/지연은 실행 예정 시각을 함께 표기
      if ((t.status === "scheduled" || t.status === "delayed") && t.scheduled_at) {
        label += " · " + new Date(t.scheduled_at * 1000).toLocaleString("ko-KR",
          { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
      }
      const badge = `<span class="tx-status tx-${escapeHtml(t.status)}">${label}</span>`;
      // 예약/지연 건은 취소 버튼
      const cancelBtn = (t.status === "scheduled" || t.status === "delayed")
        ? ` <button class="bo-cancel-btn" type="button" data-tf-id="${t.id}">취소</button>` : "";
      return `<tr><td>${t.id}</td><td>${escapeHtml(t.from_account)}</td>` +
        `<td>${escapeHtml(t.to_account)}</td><td>${won(t.amount)}</td><td>${won(t.fee)}</td>` +
        `<td>${badge}${cancelBtn}</td><td>${d}</td></tr>`;
    })
    .join("");
  const tbody = document.getElementById("bo-transfer-rows");
  tbody.innerHTML = reset ? rows : tbody.innerHTML + rows;
}

/* 예약/지연 이체 취소 (Backoffice) */
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".bo-cancel-btn");
  if (!btn) return;
  const id = btn.dataset.tfId;
  btn.disabled = true;
  try {
    const res = await apiFetch(`/api/transfers/${id}/cancel`, { method: "POST" });
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({}));
      throw new Error(detail || "취소 실패");
    }
    loadBoTransfers(true);   // 목록 새로고침
  } catch (err) {
    btn.disabled = false;
    alert("취소 실패: " + err.message);
  }
});

document.addEventListener("click", (e) => {
  const filterBtn = e.target.closest(".bo-filter-btn");
  if (filterBtn) {
    document.querySelectorAll(".bo-filter-btn").forEach((b) => b.classList.toggle("active", b === filterBtn));
    boTransferStatus = filterBtn.dataset.status;
    loadBoTransfers(true);
  }
  if (e.target.closest("#bo-transfer-more")) loadBoTransfers(false);
});

/* 이용통계: 요약(전체/조회/검색) + 최근 14일 추이 */
async function loadBoUsageStats() {
  try {
    const res = await apiFetch("/api/admin/usage-stats");
    if (!res.ok) throw new Error("이용통계 조회 실패");
    const { summary, daily } = await res.json();
    renderBoUsageSummary(summary);
    renderBoUsageDaily(daily);
  } catch (err) {
    console.error("이용통계 로드 실패:", err);
  }
}

function renderBoUsageSummary(s) {
  document.getElementById("bo-usage-summary").innerHTML = `
    <div class="metric"><div class="value">${s.total}</div><div class="label">총 이벤트</div></div>
    <div class="metric"><div class="value">${s.view}</div><div class="label">조회</div></div>
    <div class="metric"><div class="value">${s.search}</div><div class="label">검색</div></div>`;
}

function renderBoUsageDaily(daily, targetId = "bo-usage-daily") {
  const box = document.getElementById(targetId);
  if (!daily.length) { box.innerHTML = statEmpty(); return; }
  const max = Math.max(1, ...daily.map((d) => d.count));
  box.innerHTML = daily
    .map(
      (d) =>
        `<div class="bar-row">` +
        `<span class="name">${d.day}</span>` +
        `<div class="bar-track"><div class="bar-fill" style="width:${(d.count / max) * 100}%"></div></div>` +
        `<span class="num">${d.count}</span></div>`
    )
    .join("");
}

/* 성능관리: 시스템 상태 + 배치 테스트 성능 스냅샷 */
async function loadBoHealth() {
  const box = document.getElementById("bo-health-cards");
  box.textContent = "불러오는 중…";
  try {
    const res = await apiFetch("/api/admin/health");
    if (!res.ok) throw new Error("상태 조회 실패");
    const { checks } = await res.json();
    box.innerHTML = checks
      .map(
        (c) =>
          `<div class="status-card"><div class="status-card-head"><b>${escapeHtml(c.name)}</b>` +
          `<span class="status-badge ${c.status}">${{ ok: "정상", warn: "주의", down: "연결안됨" }[c.status] || c.status}</span></div>` +
          `<p>${escapeHtml(c.detail)}</p></div>`
      )
      .join("");
  } catch (err) {
    box.textContent = "상태를 불러오지 못했습니다.";
    console.error("시스템 상태 로드 실패:", err);
  }
}

async function loadBoBatchPerf() {
  const box = document.getElementById("bo-batch-perf");
  try {
    const res = await fetch("data/stats.json");
    const { quality } = await res.json();
    if (!quality) { box.innerHTML = statEmpty(); return; }
    const cats = (quality.categories || [])
      .map((c) => `<li><span class="p-name">${escapeHtml(c.name)}</span><span class="p-count">${c.count}</span></li>`)
      .join("");
    box.innerHTML = `
      <div class="metric-grid">
        <div class="metric"><div class="value">${quality.total}</div><div class="label">총 테스트</div></div>
        <div class="metric"><div class="value">${quality.success_rate}%</div><div class="label">성공률</div></div>
        <div class="metric"><div class="value">${quality.avg_latency_ms}ms</div><div class="label">평균 지연</div></div>
      </div>
      <p class="tf-hint">${escapeHtml(quality.provider)} · ${escapeHtml(quality.model)} ·
        ${escapeHtml(quality.tested_at)}</p>
      <ol class="topcat-list">${cats}</ol>`;
  } catch (err) {
    box.innerHTML = statError();
    console.error("배치 테스트 성능 로드 실패:", err);
  }
}

/* AI챗봇 설정 (제공자/모델/답변스타일/시스템프롬프트/웹검색) — Backoffice 성능관리 */
let boChatbotProviders = {};

async function loadBoChatbotConfig() {
  const statusEl = document.getElementById("bo-cc-status");
  try {
    const res = await apiFetch("/api/admin/chatbot-config");
    if (!res.ok) throw new Error("챗봇 설정을 불러오지 못했습니다.");
    const { config: cfg, providers, styles } = await res.json();
    boChatbotProviders = providers;

    const providerSel = document.getElementById("bo-cc-provider");
    providerSel.innerHTML = Object.keys(providers)
      .map((p) => `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`)
      .join("");
    providerSel.value = cfg.provider;
    fillBoChatbotModels(cfg.provider, cfg.default_model);

    const styleSel = document.getElementById("bo-cc-style");
    styleSel.innerHTML = styles
      .map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`)
      .join("");
    styleSel.value = cfg.default_style;

    document.getElementById("bo-cc-prompt").value = cfg.system_prompt || "";
    document.getElementById("bo-cc-websearch").checked = !!cfg.web_search;
  } catch (err) {
    if (statusEl) { statusEl.className = "tf-status err"; statusEl.textContent = err.message; }
    console.error("챗봇 설정 로드 실패:", err);
  }
}

function fillBoChatbotModels(provider, selected) {
  const modelSel = document.getElementById("bo-cc-model");
  const models = (boChatbotProviders[provider] && boChatbotProviders[provider].models) || [];
  modelSel.innerHTML = models
    .map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`)
    .join("");
  if (models.includes(selected)) modelSel.value = selected;
}

document.addEventListener("change", (e) => {
  if (e.target.id !== "bo-cc-provider") return;
  fillBoChatbotModels(e.target.value, "");
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "bo-chatbot-config-form") return;
  e.preventDefault();
  const statusEl = document.getElementById("bo-cc-status");
  statusEl.className = "tf-status";
  statusEl.textContent = "저장 중…";
  try {
    const res = await apiFetch("/api/admin/chatbot-config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: document.getElementById("bo-cc-provider").value,
        default_model: document.getElementById("bo-cc-model").value,
        default_style: document.getElementById("bo-cc-style").value,
        system_prompt: document.getElementById("bo-cc-prompt").value,
        web_search: document.getElementById("bo-cc-websearch").checked,
      }),
    });
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({}));
      throw new Error(detail || "저장에 실패했습니다.");
    }
    statusEl.className = "tf-status ok";
    statusEl.textContent = "저장되었습니다.";
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* ── Backoffice: 이체 정책(한도/수수료) ──────────────────────────── */
async function loadBoTransferPolicy() {
  const statusEl = document.getElementById("bo-tp-status");
  try {
    const res = await apiFetch("/api/admin/transfer-policy");
    if (!res.ok) throw new Error("이체 정책 조회 실패");
    const p = await res.json();
    document.getElementById("bo-tp-once").value = p.transfer_limit;
    document.getElementById("bo-tp-daily").value = p.daily_transfer_limit;
    document.getElementById("bo-tp-fee").value = p.transfer_fee;
  } catch (err) {
    if (statusEl) { statusEl.className = "tf-status err"; statusEl.textContent = err.message; }
    console.error("이체 정책 로드 실패:", err);
  }
}

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "bo-transfer-policy-form") return;
  e.preventDefault();
  const statusEl = document.getElementById("bo-tp-status");
  statusEl.className = "tf-status";
  statusEl.textContent = "저장 중…";
  try {
    const res = await apiFetch("/api/admin/transfer-policy", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        transfer_limit: Number(document.getElementById("bo-tp-once").value),
        daily_transfer_limit: Number(document.getElementById("bo-tp-daily").value),
        transfer_fee: Number(document.getElementById("bo-tp-fee").value),
      }),
    });
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({}));
      throw new Error(detail || "저장에 실패했습니다.");
    }
    statusEl.className = "tf-status ok";
    statusEl.textContent = "저장되었습니다.";
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* ── Backoffice: 공지사항 관리 ───────────────────────────────────── */
let boNoticeOffset = 0, boNoticeQuery = "";

async function loadBoNotices(reset = true) {
  if (reset) boNoticeOffset = 0;
  try {
    const res = await apiFetch(
      `/api/notices?offset=${boNoticeOffset}&limit=${BO_PAGE_SIZE}&q=${encodeURIComponent(boNoticeQuery)}`
    );
    if (!res.ok) throw new Error("공지사항 목록 조회 실패");
    const data = await res.json();
    renderBoNoticeRows(data.notices, reset);
    document.getElementById("bo-notice-more").style.display =
      boNoticeOffset + data.notices.length < data.total ? "" : "none";
    boNoticeOffset += data.notices.length;
  } catch (err) {
    console.error("공지사항 목록 로드 실패:", err);
  }
}

function renderBoNoticeRows(notices, reset) {
  const rows = notices
    .map((n) => {
      const d = new Date(n.created_at * 1000).toLocaleDateString("ko-KR");
      return `<tr><td>${escapeHtml(n.title)}</td><td>${d}</td>` +
        `<td><button class="btn btn-ghost bo-del-btn" type="button" data-kind="notice" data-id="${n.id}">삭제</button></td></tr>`;
    })
    .join("");
  const tbody = document.getElementById("bo-notice-rows");
  tbody.innerHTML = reset ? rows : tbody.innerHTML + rows;
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#bo-notice-search-btn")) {
    boNoticeQuery = document.getElementById("bo-notice-search").value.trim();
    loadBoNotices(true);
  }
  if (e.target.closest("#bo-notice-more")) loadBoNotices(false);
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "bo-notice-form") return;
  e.preventDefault();
  const statusEl = document.getElementById("bo-notice-form-status");
  statusEl.className = "tf-status";
  statusEl.textContent = "등록 중…";
  try {
    const res = await apiFetch("/api/admin/notices", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: document.getElementById("bo-notice-title").value.trim(),
        content: document.getElementById("bo-notice-content").value.trim(),
      }),
    });
    if (!res.ok) throw new Error("등록에 실패했습니다.");
    statusEl.textContent = "";
    e.target.reset();
    loadBoNotices(true);
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* ── Backoffice: FAQ 관리 ───────────────────────────────────────── */
let boFaqOffset = 0, boFaqQuery = "";

async function loadBoFaqs(reset = true) {
  if (reset) boFaqOffset = 0;
  try {
    const res = await apiFetch(
      `/api/faqs?offset=${boFaqOffset}&limit=${BO_PAGE_SIZE}&q=${encodeURIComponent(boFaqQuery)}`
    );
    if (!res.ok) throw new Error("FAQ 목록 조회 실패");
    const data = await res.json();
    renderBoFaqRows(data.faqs, reset);
    document.getElementById("bo-faq-more").style.display =
      boFaqOffset + data.faqs.length < data.total ? "" : "none";
    boFaqOffset += data.faqs.length;
  } catch (err) {
    console.error("FAQ 목록 로드 실패:", err);
  }
}

function renderBoFaqRows(faqs, reset) {
  const rows = faqs
    .map((f) => {
      const d = new Date(f.created_at * 1000).toLocaleDateString("ko-KR");
      return `<tr><td>${escapeHtml(f.question)}</td><td>${d}</td>` +
        `<td><button class="btn btn-ghost bo-del-btn" type="button" data-kind="faq" data-id="${f.id}">삭제</button></td></tr>`;
    })
    .join("");
  const tbody = document.getElementById("bo-faq-rows");
  tbody.innerHTML = reset ? rows : tbody.innerHTML + rows;
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#bo-faq-search-btn")) {
    boFaqQuery = document.getElementById("bo-faq-search").value.trim();
    loadBoFaqs(true);
  }
  if (e.target.closest("#bo-faq-more")) loadBoFaqs(false);
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "bo-faq-form") return;
  e.preventDefault();
  const statusEl = document.getElementById("bo-faq-form-status");
  statusEl.className = "tf-status";
  statusEl.textContent = "등록 중…";
  try {
    const res = await apiFetch("/api/admin/faqs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: document.getElementById("bo-faq-question").value.trim(),
        answer: document.getElementById("bo-faq-answer").value.trim(),
      }),
    });
    if (!res.ok) throw new Error("등록에 실패했습니다.");
    statusEl.textContent = "";
    e.target.reset();
    loadBoFaqs(true);
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* ── Backoffice: 서식·약관·설명서 관리 ─────────────────────────────── */
let boDocOffset = 0, boDocQuery = "";

async function loadBoDocuments(reset = true) {
  if (reset) boDocOffset = 0;
  try {
    const res = await apiFetch(
      `/api/documents?offset=${boDocOffset}&limit=${BO_PAGE_SIZE}&q=${encodeURIComponent(boDocQuery)}`
    );
    if (!res.ok) throw new Error("서식자료 목록 조회 실패");
    const data = await res.json();
    renderBoDocRows(data.documents, reset);
    document.getElementById("bo-doc-more").style.display =
      boDocOffset + data.documents.length < data.total ? "" : "none";
    boDocOffset += data.documents.length;
  } catch (err) {
    console.error("서식자료 목록 로드 실패:", err);
  }
}

function renderBoDocRows(docs, reset) {
  const rows = docs
    .map((doc) => {
      const d = new Date(doc.created_at * 1000).toLocaleDateString("ko-KR");
      return `<tr><td>${escapeHtml(doc.title)}</td><td>${escapeHtml(doc.category)}</td><td>${d}</td>` +
        `<td><button class="btn btn-ghost bo-del-btn" type="button" data-kind="document" data-id="${doc.id}">삭제</button></td></tr>`;
    })
    .join("");
  const tbody = document.getElementById("bo-doc-rows");
  tbody.innerHTML = reset ? rows : tbody.innerHTML + rows;
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#bo-doc-search-btn")) {
    boDocQuery = document.getElementById("bo-doc-search").value.trim();
    loadBoDocuments(true);
  }
  if (e.target.closest("#bo-doc-more")) loadBoDocuments(false);
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "bo-document-form") return;
  e.preventDefault();
  const statusEl = document.getElementById("bo-doc-form-status");
  statusEl.className = "tf-status";
  statusEl.textContent = "등록 중…";
  try {
    const res = await apiFetch("/api/admin/documents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: document.getElementById("bo-doc-title").value.trim(),
        category: document.getElementById("bo-doc-category").value,
        description: document.getElementById("bo-doc-desc").value.trim(),
      }),
    });
    if (!res.ok) throw new Error("등록에 실패했습니다.");
    statusEl.textContent = "";
    e.target.reset();
    loadBoDocuments(true);
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* 공지사항/FAQ/서식자료 공용 삭제 버튼 */
const BO_DELETE_ENDPOINTS = {
  notice: { url: (id) => `/api/admin/notices/${id}`, reload: () => loadBoNotices(true), label: "공지사항" },
  faq: { url: (id) => `/api/admin/faqs/${id}`, reload: () => loadBoFaqs(true), label: "FAQ" },
  document: { url: (id) => `/api/admin/documents/${id}`, reload: () => loadBoDocuments(true), label: "서식자료" },
};

document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".bo-del-btn");
  if (!btn) return;
  const entry = BO_DELETE_ENDPOINTS[btn.dataset.kind];
  if (!entry) return;
  if (!confirm(`이 ${entry.label}을(를) 삭제할까요?`)) return;
  try {
    const res = await apiFetch(entry.url(btn.dataset.id), { method: "DELETE" });
    if (!res.ok) throw new Error("삭제에 실패했습니다.");
    entry.reload();
  } catch (err) {
    console.error("삭제 실패:", err);
    alert(err.message);
  }
});

/* ── Backoffice: 시스템설정 (시스템 현황 + 인프라 설정 읽기전용) ───── */
async function loadBoSystemOverview() {
  const box = document.getElementById("bo-sys-overview");
  try {
    const [usersRes, transfersRes, usageRes] = await Promise.all([
      apiFetch("/api/admin/users?limit=1"),
      apiFetch("/api/admin/transfers?limit=1"),
      apiFetch("/api/admin/usage-stats"),
    ]);
    const users = await usersRes.json();
    const transfers = await transfersRes.json();
    const usage = await usageRes.json();
    box.innerHTML = `
      <div class="metric"><div class="value">${users.total}</div><div class="label">총 회원수</div></div>
      <div class="metric"><div class="value">${users.account_count}</div><div class="label">총 계좌수</div></div>
      <div class="metric"><div class="value">${won(users.total_balance)}</div><div class="label">총 예치금</div></div>
      <div class="metric"><div class="value">${transfers.summary.total}</div><div class="label">총 이체건수</div></div>
      <div class="metric"><div class="value">${won(transfers.summary.completed_amount || 0)}</div><div class="label">이체 완료금액</div></div>
      <div class="metric"><div class="value">${usage.summary.total}</div><div class="label">총 이용이벤트</div></div>`;
  } catch (err) {
    box.innerHTML = statError();
    console.error("시스템 현황 로드 실패:", err);
  }
}

async function loadBoInfraConfig() {
  const box = document.getElementById("bo-infra-config");
  try {
    const res = await apiFetch("/api/admin/infra-config");
    if (!res.ok) throw new Error("인프라 설정 조회 실패");
    const cfg = await res.json();
    const rows = [
      ["시맨틱 캐시", cfg.cache_enabled ? "사용" : "미사용"],
      ["RAG 검색 결과 수", cfg.rag_top_k],
      ["캐시 유사도 임계값", cfg.cache_threshold],
      ["Redis TTL (초)", cfg.redis_ttl],
      ["Elasticsearch 주소", cfg.es_host],
      ["Redis 주소", `${cfg.redis_host}:${cfg.redis_port}`],
    ];
    box.innerHTML = `<ol class="topcat-list">${rows
      .map(([label, value]) => `<li><span class="p-name">${escapeHtml(label)}</span><span class="p-count">${escapeHtml(String(value))}</span></li>`)
      .join("")}</ol>`;
  } catch (err) {
    box.innerHTML = statError();
    console.error("인프라 설정 로드 실패:", err);
  }
}

/* ── 고객센터: 공지사항 / FAQ / 문의하기 / 서식·약관·설명서 ─────────── */
const SUPPORT_TABS = ["notices", "faq", "inquiry", "documents"];
const supportLoaded = {};
let noticeOffset = 0, noticeQuery = "";
let faqOffset = 0, faqQuery = "";
let documentOffset = 0, documentQuery = "";
const SUPPORT_PAGE_SIZE = 10;

function supportGoTab(name) {
  if (!SUPPORT_TABS.includes(name)) name = "notices";
  document.querySelectorAll(".support-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.supportTab === name)
  );
  SUPPORT_TABS.forEach((t) => {
    document.getElementById(`support-panel-${t}`).style.display = t === name ? "block" : "none";
  });
  ensureSupportTabLoaded(name);
}

function ensureSupportTabLoaded(name) {
  if (name === "inquiry") { updateInquiryView(); return; }   // 로그인 상태가 바뀔 수 있어 매번 갱신
  if (supportLoaded[name]) return;
  supportLoaded[name] = true;
  if (name === "notices") loadNotices();
  if (name === "faq") loadFaqs();
  if (name === "documents") loadDocuments();
}

document.addEventListener("click", (e) => {
  const tab = e.target.closest(".support-tab");
  if (tab) supportGoTab(tab.dataset.supportTab);
});

const fmtDate = (t) => new Date(t * 1000).toLocaleDateString("ko-KR");

/* 공지사항 */
async function loadNotices(reset = true) {
  if (reset) noticeOffset = 0;
  try {
    const res = await fetch(`/api/notices?offset=${noticeOffset}&limit=${SUPPORT_PAGE_SIZE}&q=${encodeURIComponent(noticeQuery)}`);
    const data = await res.json();
    renderNoticeList(data.notices, reset);
    document.getElementById("notice-more").style.display =
      noticeOffset + data.notices.length < data.total ? "" : "none";
    noticeOffset += data.notices.length;
  } catch (err) {
    console.error("공지사항 로드 실패:", err);
  }
}

function renderNoticeList(notices, reset) {
  const rows = notices
    .map(
      (n) =>
        `<div class="faq-item"><div class="faq-q">${escapeHtml(n.title)} <span class="chev">▾</span></div>` +
        `<div class="faq-a"><div class="tf-hint">${fmtDate(n.created_at)}</div>${escapeHtml(n.content)}</div></div>`
    )
    .join("");
  const box = document.getElementById("notice-list");
  if (reset) box.innerHTML = notices.length ? rows : statEmpty();
  else box.innerHTML += rows;
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#notice-search-btn")) {
    noticeQuery = document.getElementById("notice-search").value.trim();
    loadNotices(true);
  }
  if (e.target.closest("#notice-more")) loadNotices(false);
});

/* FAQ */
async function loadFaqs(reset = true) {
  if (reset) faqOffset = 0;
  try {
    const res = await fetch(`/api/faqs?offset=${faqOffset}&limit=${SUPPORT_PAGE_SIZE}&q=${encodeURIComponent(faqQuery)}`);
    const data = await res.json();
    renderFaqList(data.faqs, reset);
    document.getElementById("faq-more").style.display =
      faqOffset + data.faqs.length < data.total ? "" : "none";
    faqOffset += data.faqs.length;
  } catch (err) {
    console.error("FAQ 로드 실패:", err);
  }
}

function renderFaqList(faqs, reset) {
  const rows = faqs
    .map(
      (f) =>
        `<div class="faq-item"><div class="faq-q">${escapeHtml(f.question)} <span class="chev">▾</span></div>` +
        `<div class="faq-a">${escapeHtml(f.answer)}</div></div>`
    )
    .join("");
  const box = document.getElementById("faq-list");
  if (reset) box.innerHTML = faqs.length ? rows : statEmpty();
  else box.innerHTML += rows;
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#faq-search-btn")) {
    faqQuery = document.getElementById("faq-search").value.trim();
    loadFaqs(true);
  }
  if (e.target.closest("#faq-more")) loadFaqs(false);
});

/* 서식·약관·설명서 */
async function loadDocuments(reset = true) {
  if (reset) documentOffset = 0;
  try {
    const res = await fetch(`/api/documents?offset=${documentOffset}&limit=${SUPPORT_PAGE_SIZE}&q=${encodeURIComponent(documentQuery)}`);
    const data = await res.json();
    renderDocumentList(data.documents, reset);
    document.getElementById("document-more").style.display =
      documentOffset + data.documents.length < data.total ? "" : "none";
    documentOffset += data.documents.length;
  } catch (err) {
    console.error("서식·약관·설명서 로드 실패:", err);
  }
}

function renderDocumentList(documents, reset) {
  const rows = documents
    .map(
      (d) =>
        `<div class="document-item"><div class="doc-title">${escapeHtml(d.title)} ` +
        `<span class="doc-badge">${escapeHtml(d.category)}</span></div>` +
        `<p>${escapeHtml(d.description)}</p></div>`
    )
    .join("");
  const box = document.getElementById("document-list");
  if (reset) box.innerHTML = documents.length ? rows : statEmpty();
  else box.innerHTML += rows;
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#document-search-btn")) {
    documentQuery = document.getElementById("document-search").value.trim();
    loadDocuments(true);
  }
  if (e.target.closest("#document-more")) loadDocuments(false);
});

/* 문의하기 (로그인 필수) */
function updateInquiryView() {
  const logged = isLoggedIn();
  document.getElementById("inquiry-guest").style.display = logged ? "none" : "";
  document.getElementById("inquiry-authed").style.display = logged ? "" : "none";
  if (logged) loadInquiries();
}

async function loadInquiries() {
  try {
    const res = await apiFetch("/api/inquiries");
    if (!res.ok) throw new Error("문의 내역 조회 실패");
    const { inquiries } = await res.json();
    const box = document.getElementById("inquiry-list");
    box.innerHTML = inquiries.length
      ? inquiries
          .map(
            (q) =>
              `<div class="document-item"><div class="doc-title">${escapeHtml(q.title)}</div>` +
              `<div class="tf-hint">${fmtDate(q.created_at)}</div><p>${escapeHtml(q.content)}</p></div>`
          )
          .join("")
      : statEmpty();
  } catch (err) {
    console.error("문의 내역 로드 실패:", err);
  }
}

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "inquiry-form") return;
  e.preventDefault();
  const title = document.getElementById("inquiry-title").value.trim();
  const content = document.getElementById("inquiry-content").value.trim();
  const statusEl = document.getElementById("inquiry-status");
  statusEl.className = "tf-status";
  statusEl.textContent = "등록 중…";
  try {
    const res = await apiFetch("/api/inquiries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, content }),
    });
    if (!res.ok) throw new Error("문의 등록에 실패했습니다.");
    statusEl.textContent = "";
    e.target.reset();
    loadInquiries();
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* ── FAQ 아코디언(공지사항·FAQ 공통 클릭 위임) ──────────────────────── */
document.addEventListener("click", (e) => {
  const q = e.target.closest(".faq-q");
  if (!q) return;
  q.parentElement.classList.toggle("open");
});

/* ── 유틸 ────────────────────────────────────────────────────────── */
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ── 맨 위로 이동 플로팅 버튼 (스크롤 300px 이상 시 노출) ─────────── */
(function () {
  const btn = document.getElementById("scroll-top");
  if (!btn) return;
  const toggle = () => btn.classList.toggle("show", window.scrollY > 300);
  window.addEventListener("scroll", toggle, { passive: true });
  btn.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  toggle();
})();

/* ── 챗봇 iframe → 부모 SPA 이동 (이체내역 조회하기 등) ───────────── */
window.addEventListener("message", (e) => {
  const d = e.data;
  if (!d || d.type !== "goto-account") return;   // 내부 네비게이션 메시지만 처리
  navigate("account");
  const acctNo = d.account_no;
  if (!acctNo) return;
  // 계좌 목록이 렌더된 뒤 해당 계좌의 거래내역 자동 열기
  let tries = 0;
  const open = () => {
    const card = document.querySelector(`.acct-card[data-acct-no="${CSS.escape(acctNo)}"]`);
    if (card) {
      showTransactions(card.dataset.acctId, card.dataset.acctNo);
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } else if (tries++ < 25) {
      setTimeout(open, 150);
    }
  };
  open();
});

/* ── 초기 진입 (해시 기반) ───────────────────────────────────────── */
fillSignupBanks();
refreshAuthUI();
navigate((location.hash || "#home").slice(1));
window.addEventListener("hashchange", () =>
  navigate((location.hash || "#home").slice(1))
);
