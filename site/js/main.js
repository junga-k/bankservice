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
}

function updateAuthSectionView() {
  const auth = getAuth();
  const logged = !!auth?.token;
  document.getElementById("auth-forms").style.display = logged ? "none" : "";
  document.getElementById("auth-welcome").style.display = logged ? "" : "none";
  if (!logged) return;
  document.getElementById("auth-welcome-title").textContent = `환영합니다, ${auth.name}님`;
}

function setAuthTab(tab) {
  document.querySelectorAll(".auth-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.authTab === tab)
  );
  document.getElementById("login-form").style.display = tab === "login" ? "" : "none";
  document.getElementById("signup-form").style.display = tab === "signup" ? "" : "none";
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
    ensureBoTabLoaded("members");
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
function ensureChatLoaded() {
  if (chatLoaded) return;
  const wrap = document.getElementById("chat-frame-wrap");
  const iframe = document.createElement("iframe");
  iframe.src = CHAT_URL;
  iframe.title = "AI 금융상담 챗봇";
  iframe.allow = "clipboard-write";
  wrap.appendChild(iframe);
  chatLoaded = true;
}

/* ── 은행 로고 배지 ──────────────────────────────────────────────── */
/* 실제 로고 이미지 대신 은행 브랜드 컬러 기반 이니셜 배지로 대체(상표권 부담 없이 구분 가능) */
const BANK_BRAND = {
  "신한은행":    { label: "신한", color: "#0046FF" },
  "국민은행":    { label: "KB",  color: "#FFB300" },
  "KB국민은행":  { label: "KB",  color: "#FFB300" },
  "우리은행":    { label: "우리", color: "#0067AC" },
  "하나은행":    { label: "하나", color: "#00857C" },
  "농협은행":    { label: "NH",  color: "#00A651" },
  "NH농협은행":  { label: "NH",  color: "#00A651" },
  "IBK기업은행": { label: "IBK", color: "#0072BC" },
  "카카오뱅크":  { label: "카카오", color: "#FFCD00" },
  "토스뱅크":    { label: "토스", color: "#0064FF" },
  "케이뱅크":    { label: "케이", color: "#FF5F3B" },
  "SC제일은행":  { label: "SC",  color: "#12A0D7" },
  "부산은행":    { label: "부산", color: "#004EA2" },
  "대구은행":    { label: "대구", color: "#EE7D1F" },
  "경남은행":    { label: "경남", color: "#009944" },
  "광주은행":    { label: "광주", color: "#F58220" },
  "전북은행":    { label: "전북", color: "#EE3524" },
  "제주은행":    { label: "제주", color: "#00AEEF" },
};
const BANK_FALLBACK_COLORS = ["#5F6368", "#7B61FF", "#00838F", "#8D6E63", "#546E7A"];

function bankBadge(name) {
  const brand = BANK_BRAND[name];
  if (brand) {
    return `<span class="bank-badge" style="background:${brand.color}">${escapeHtml(brand.label)}</span>`;
  }
  // 매핑에 없는 은행: 이름 기반 해시로 고정 색상 + 첫 글자
  const hash = [...(name || "?")].reduce((h, c) => h + c.charCodeAt(0), 0);
  const color = BANK_FALLBACK_COLORS[hash % BANK_FALLBACK_COLORS.length];
  const label = (name || "?").slice(0, 2);
  return `<span class="bank-badge" style="background:${color}">${escapeHtml(label)}</span>`;
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
    holderEl.className = "tf-holder ok";
    holderEl.innerHTML = `✅ <b>${escapeHtml(data.holder_name)}</b> (${escapeHtml(data.bank_name)})` +
      (data.fee ? ` · 타행 수수료 ${won(data.fee)}` : " · 수수료 면제");
    showModal(`
      <h3>받는 분 확인</h3>
      <div class="cf-row"><span>예금주</span><b>${escapeHtml(data.holder_name)}</b></div>
      <div class="cf-row"><span>은행명</span><b>${escapeHtml(data.bank_name)}</b></div>
      <div class="cf-row"><span>계좌번호</span><b>${escapeHtml(data.account_no)}</b></div>
      <div class="cf-row"><span>수수료</span><b>${data.fee ? won(data.fee) : "면제"}</b></div>
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
  const statusEl = document.getElementById("tf-status2");
  statusEl.className = "tf-status";
  statusEl.textContent = "이체 요청 중…";
  e.target.disabled = true;

  try {
    const res = await apiFetch("/api/transfer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        from_account: acc.account_no, to_account: tfVerified.account_no,
        amount, memo: memo || null, sender_memo: senderMemo || null,
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
  loadBankRanking("stat-banks", 5);
  loadTopProductsCarousel();
}

async function loadBankRanking(targetId = "stat-banks", limit = 10) {
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
          `<span class="num">${b.count}</span></div>`
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

/* 상품안내 페이지 전용: 예금/적금/금리비교 3개 카테고리 Top5 캐러셀 */
const STAT_PRODUCT_CATS = ["예금", "적금", "금리비교"];
let statProductsIndex = 0;

async function loadTopProductsCarousel(targetId = "stat-products") {
  const box = document.getElementById(targetId);
  try {
    const res = await fetch("/api/stats/top-products");
    const { categories } = await res.json();
    const hasAny = STAT_PRODUCT_CATS.some((c) => (categories[c] || []).length > 0);
    if (!hasAny) {
      box.innerHTML = statEmpty();
      return;
    }
    const slides = STAT_PRODUCT_CATS.map((cat) => {
      const items = categories[cat] || [];
      const body = items.length
        ? `<ol class="topcat-list">${items
            .map(
              (p, i) =>
                `<li><span class="rank">${i + 1}</span>` +
                `<span class="p-name">${escapeHtml(p.product)}</span></li>`
            )
            .join("")}</ol>`
        : `<p class="stat-empty">아직 조회 데이터가 없습니다.</p>`;
      return `<div class="stat-products-slide"><div class="topcat"><div class="topcat-title">${escapeHtml(cat)}</div>${body}</div></div>`;
    }).join("");
    const dots = STAT_PRODUCT_CATS.map(
      (cat, i) =>
        `<button class="stat-products-dot${i === 0 ? " active" : ""}" type="button" data-slide="${i}" aria-label="${escapeHtml(cat)}"></button>`
    ).join("");
    box.innerHTML =
      `<div class="stat-products-carousel">` +
      `<button class="stat-products-nav prev" type="button" aria-label="이전 카테고리">‹</button>` +
      `<div class="stat-products-track" id="stat-products-track">${slides}</div>` +
      `<button class="stat-products-nav next" type="button" aria-label="다음 카테고리">›</button>` +
      `</div>` +
      `<div class="stat-products-dots">${dots}</div>`;
    statProductsIndex = 0;
    goStatProductsSlide(0);
  } catch (err) {
    box.innerHTML = statError();
    console.error("인기 상품 로드 실패:", err);
  }
}

function goStatProductsSlide(i) {
  const track = document.getElementById("stat-products-track");
  const dots = document.querySelectorAll(".stat-products-dot");
  if (!track || !dots.length) return;
  statProductsIndex = (i + dots.length) % dots.length;
  track.style.transform = `translateX(-${statProductsIndex * 100}%)`;
  dots.forEach((d, idx) => d.classList.toggle("active", idx === statProductsIndex));
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
document.addEventListener("click", (e) => {
  const tab = e.target.closest(".auth-tab");
  if (tab) setAuthTab(tab.dataset.authTab);
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
  statusEl.textContent = "가입 처리 중…";
  try {
    const res = await fetch("/api/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, name }),
    });
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({}));
      throw new Error(detail || "회원가입에 실패했습니다.");
    }
    const data = await res.json();
    setAuth(data);
    statusEl.textContent = "";
    document.getElementById("signup-form").reset();
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
const BO_TABS = ["members", "transfers", "usage", "perf", "products", "faq", "settings"];
const boLoaded = {};   // 탭별 최초 로드 여부(지연 로드)
let boUserOffset = 0;
let boUserQuery = "";
let boTransferOffset = 0;
let boTransferStatus = "";
const BO_PAGE_SIZE = 20;

function boGoTab(name) {
  if (!BO_TABS.includes(name)) name = "members";
  document.querySelectorAll(".bo-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.boTab === name)
  );
  BO_TABS.forEach((t) => {
    document.getElementById(`bo-panel-${t}`).style.display = t === name ? "block" : "none";
  });
  ensureBoTabLoaded(name);
}

function ensureBoTabLoaded(name) {
  if (boLoaded[name]) return;
  boLoaded[name] = true;
  if (name === "members") loadBoUsers();
  if (name === "transfers") loadBoTransfers();
  if (name === "usage") {
    loadBoUsageStats();
    loadBankRanking("bo-stat-banks");
    loadTopProducts("bo-stat-products");
  }
  if (name === "perf") { loadBoHealth(); loadBoBatchPerf(); }
}

document.addEventListener("click", (e) => {
  const tab = e.target.closest(".bo-tab");
  if (tab) boGoTab(tab.dataset.boTab);
});

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
    <div class="metric"><div class="value">${won(s.completed_amount)}</div><div class="label">완료 금액</div></div>`;
}

const BO_STATUS_LABEL = { completed: "완료", pending: "대기", failed: "실패" };

function renderBoTransferRows(transfers, reset) {
  const rows = transfers
    .map((t) => {
      const d = new Date(t.created_at * 1000).toLocaleString("ko-KR");
      return `<tr><td>${t.id}</td><td>${escapeHtml(t.from_account)}</td>` +
        `<td>${escapeHtml(t.to_account)}</td><td>${won(t.amount)}</td><td>${won(t.fee)}</td>` +
        `<td>${BO_STATUS_LABEL[t.status] || escapeHtml(t.status)}</td><td>${d}</td></tr>`;
    })
    .join("");
  const tbody = document.getElementById("bo-transfer-rows");
  tbody.innerHTML = reset ? rows : tbody.innerHTML + rows;
}

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

function renderBoUsageDaily(daily) {
  const box = document.getElementById("bo-usage-daily");
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

/* ── 초기 진입 (해시 기반) ───────────────────────────────────────── */
refreshAuthUI();
navigate((location.hash || "#home").slice(1));
window.addEventListener("hashchange", () =>
  navigate((location.hash || "#home").slice(1))
);
