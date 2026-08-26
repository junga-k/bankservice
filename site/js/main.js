/* ── 매치뱅크 홈페이지 클라이언트 로직 ───────────────────────────
 * - 네비게이션: 헤더 고정, 본문 <section>만 토글 (SPA형)
 * - AI은행원: 최초 진입 시 iframe 1회 생성 후 유지 (대화 상태 보존)
 * - 대시보드/은행 목록: data/*.json fetch 후 렌더
 * - FAQ: 아코디언 토글
 */

// 배포 환경마다 다른 AI은행원(Streamlit) 주소는 js/env-config.js가 로드 시점에
// window.CHAT_BASE_URL로 미리 심어둔다(env-config.js가 아직 없거나 값이 비어있으면
// 로컬 개발 기본값인 localhost:8501로 폴백).
const CHAT_BASE_URL = window.CHAT_BASE_URL || "http://localhost:8501";
// embed=true 만 붙인다. 과거에 함께 넘기던 embedded=1 은 app.py 가 읽지도 않는데,
// Streamlit Community Cloud 에 배포하면 이 파라미터가 뷰어 인증 플로우를 트리거해
// iframe 안에서 share.streamlit.io 리다이렉트 루프(ERR_TOO_MANY_REDIRECTS)가 난다.
const CHAT_URL = `${CHAT_BASE_URL}/?embed=true`;
const SECTIONS = ["home", "account", "products", "chat", "support", "auth", "mypage", "backoffice"];

// .faq-q 아코디언 공통 chevron 아이콘(사이트 아이콘 시스템과 동일한 feather 스타일 SVG, scroll-hint와 같은 path)
const CHEV_SVG = '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';

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
  syncChatAuth();   // 로그인/로그아웃을 은행원 iframe에 반영(사이트 로그인과 연동)
}

function updateAuthSectionView() {
  const auth = getAuth();
  const logged = !!auth?.token;
  const loginPanel = document.getElementById("login-panel");
  const signupPanel = document.getElementById("signup-panel");
  document.getElementById("auth-welcome").style.display = logged ? "" : "none";
  if (logged) {
    AUTH_PANELS.forEach((t) => {
      const el = document.getElementById(`${t}-panel`);
      if (el) el.style.display = "none";
    });
    document.getElementById("auth-welcome-title").textContent = `환영합니다, ${auth.name}님`;
    return;
  }
  // 비로그인: 둘 다 숨겨져 있으면 기본 로그인 화면 표시(로그아웃 직후 등)
  if (loginPanel.style.display === "none" && signupPanel.style.display === "none") {
    setAuthTab("login");
  }
}

/* 로그인 ↔ 회원가입 ↔ 아이디찾기 ↔ 비번재설정 전환 (독립 패널 토글) */
const AUTH_PANELS = ["login", "signup", "findid", "resetpw"];
function setAuthTab(tab) {
  if (!AUTH_PANELS.includes(tab)) tab = "login";
  AUTH_PANELS.forEach((t) => {
    const el = document.getElementById(`${t}-panel`);
    if (el) el.style.display = t === tab ? "" : "none";
  });
  // 다른 패널로 이동해도 인증 상태가 남아있지 않도록 매번 초기화
  resetFindIdVerification();
  resetResetPwVerification();
  if (tab === "signup") signupGoStep(1);
  if (tab === "resetpw") resetpwGoStep(1);
}

/* 로그인 패널 내부 링크(아이디 찾기/비번 재설정 등) 전환 */
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-auth-panel]");
  if (!el) return;
  e.preventDefault();
  setAuthTab(el.dataset.authPanel);
});

/* ── 범용 모달 ───────────────────────────────────────────────────── */
function showModal(html, wide = false) {
  document.getElementById("modal-body").innerHTML = html;
  document.getElementById("modal-box").classList.toggle("wide", wide);
  document.getElementById("modal-overlay").style.display = "flex";
}
function closeModal() {
  document.getElementById("modal-overlay").style.display = "none";
  document.getElementById("modal-box").classList.remove("wide");
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

  if (name === "chat") {
    const logged = isLoggedIn();
    document.getElementById("chat-guest").style.display = logged ? "none" : "";
    document.getElementById("chat-frame-wrap").style.display = logged ? "" : "none";
    if (logged) ensureChatLoaded();
  }
  if (name === "products") { ensureBanksLoaded(); loadProductStats(); loadSpecialProducts(); }
  if (name === "account") {
    const logged = isLoggedIn();
    document.getElementById("account-guest").style.display = logged ? "none" : "";
    document.getElementById("account-authed").style.display = logged ? "" : "none";
    if (logged) { accountGoTab("inquiry"); loadAccounts(); }
  }
  if (name === "mypage") {
    const logged = isLoggedIn();
    document.getElementById("mypage-guest").style.display = logged ? "none" : "";
    document.getElementById("mypage-authed").style.display = logged ? "" : "none";
    if (logged) { mypageGoTab("profile"); }
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
  if (window.updateScrollHint) window.updateScrollHint();
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

/* 클릭 위임: data-nav 속성을 가진 모든 요소 (data-auth 있으면 로그인/회원가입 탭도, data-account-tab 있으면 내 계좌 서브탭도 맞춤) */
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-nav]");
  if (!el) return;
  e.preventDefault();
  navigate(el.dataset.nav);
  if (el.dataset.auth) setAuthTab(el.dataset.auth);
  if (el.dataset.accountTab) accountGoTab(el.dataset.accountTab);
  if (el.dataset.mypageTab) mypageGoTab(el.dataset.mypageTab);
});

/* ── 홈: 이벤트 배너 자동 전환 + 백오피스 "배너 관리" 데이터 동적 렌더 ──── */
const BANNER_INTERVAL = 4000;
let bannerIndex = 0;
let bannerTimer = null;
let bannerData = [];

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
  if (document.querySelectorAll(".banner-dot").length < 2) return;
  bannerTimer = setInterval(() => goBannerSlide(bannerIndex + 1), BANNER_INTERVAL);
}
function stopBannerAuto() {
  if (bannerTimer) clearInterval(bannerTimer);
}

async function loadHomeBanners() {
  try {
    const res = await fetch("/api/banners");
    const data = await res.json();
    bannerData = data.banners || [];
  } catch (err) {
    console.error("배너 로드 실패:", err);
    bannerData = [];
  }
  renderHomeBanners();
}

function renderHomeBanners() {
  const track = document.getElementById("banner-track");
  const dots = document.getElementById("banner-dots");
  if (!track || !dots) return;
  track.innerHTML = bannerData
    .map(
      (b) =>
        `<div class="banner-slide" data-banner-id="${b.id}">` +
        `<img src="${b.image_path}" alt="${escapeHtml(b.title)}" /></div>`
    )
    .join("");
  dots.innerHTML = bannerData
    .map(
      (b, idx) =>
        `<button class="banner-dot${idx === 0 ? " active" : ""}" type="button" data-slide="${idx}" aria-label="배너 ${idx + 1}"></button>`
    )
    .join("");
  bannerIndex = 0;
  goBannerSlide(0);
  startBannerAuto();
}

function goToBannerLink(banner) {
  if (banner.link_type === "notice") {
    navigate("support");
    supportGoTab("notices");
  } else if (banner.link_type === "event") {
    navigate("support");
    supportGoTab("events");
  } else if (banner.link_type === "special_product") {
    navigate("products");
  }
}

document.addEventListener("click", (e) => {
  const dot = e.target.closest(".banner-dot");
  if (dot) {
    goBannerSlide(Number(dot.dataset.slide));
    startBannerAuto();
    return;
  }
  const slide = e.target.closest(".banner-slide");
  if (slide) {
    const banner = bannerData.find((b) => String(b.id) === slide.dataset.bannerId);
    if (banner) goToBannerLink(banner);
  }
});

const bannerEl = document.querySelector(".event-banner");
if (bannerEl) {
  bannerEl.addEventListener("mouseenter", stopBannerAuto);
  bannerEl.addEventListener("mouseleave", startBannerAuto);
}
loadHomeBanners();

/* ── 홈: 기능 소개 스크롤 등장 ───────────────────────────────────── */
const showcaseRows = document.querySelectorAll(".showcase-row");
if (showcaseRows.length) {
  const showcaseObserver = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("in-view");
          showcaseObserver.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.2 }
  );
  showcaseRows.forEach((row) => showcaseObserver.observe(row));
}

/* ── AI은행원 iframe: 최초 1회만 생성 ─────────────────────────────── */
let chatLoaded = false;

/* 은행원 iframe URL: 사이트 로그인 토큰을 항상 token 파라미터로 전달(로그아웃 시 빈 값).
   → 은행원(:8501)이 사이트 로그인 상태를 단일 기준으로 삼아 동기화한다.
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
  iframe.title = "AI 금융상담 은행원";
  iframe.allow = "clipboard-write";
  wrap.appendChild(iframe);
  chatLoaded = true;
}

/* 로그인/로그아웃 시 은행원 iframe을 현재 토큰으로 재로딩 → 로그인 상태 연동 */
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
  "아이엠뱅크":  { label: "대구", color: "#EE7D1F", logo: "daegu.png" }, // 대구은행이 iM뱅크(아이엠뱅크)로 리브랜딩 — FSS API는 새 법정명을 반환
  "경남은행":    { label: "경남", color: "#009944", logo: "gyeongnam.png" },
  "광주은행":    { label: "광주", color: "#F58220", logo: "gwangju.png" },
  "전북은행":    { label: "전북", color: "#EE3524", logo: "jeonbuk.png" },
  "제주은행":    { label: "제주", color: "#00AEEF", logo: "jeju.png" },
  "수협은행":    { label: "수협", color: "#0C4DA2", logo: "suhyup.png" },
  "한국산업은행": { label: "KDB", color: "#1B3F94", logo: "kdb.png" },
  "한국씨티은행": { label: "씨티", color: "#EC1C24", logo: "citibank.png" },
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

/* Backoffice 지표 카드용 미니 막대 차트. 실제 데이터(날짜별 발생 건수)만 그린다 —
   시계열이 2개 미만이면 추세라 부를 게 없으므로 빈 문자열(렌더 안 함).
   최근(마지막) 막대만 accentColor로 강조하고 나머지는 CSS 기본색(de-emphasis)으로 둔다.
   titles가 있으면 막대별 title 속성으로 날짜·값을 네이티브 툴팁으로 보여준다(보너스 채널). */
function sparkBars(values, accentColor, titles) {
  if (!values || values.length < 2) return "";
  const max = Math.max(1, ...values);
  const bars = values
    .map((v, i) => {
      const pct = Math.max(6, Math.round((v / max) * 100));
      const isNow = i === values.length - 1;
      const style = isNow ? `height:${pct}%;background:${accentColor}` : `height:${pct}%`;
      const title = titles && titles[i] ? ` title="${escapeHtml(titles[i])}"` : "";
      return `<div class="bar${isNow ? " now" : ""}" style="${style}"${title}></div>`;
    })
    .join("");
  return `<div class="spark-bars">${bars}</div>`;
}

/* "YYYY-MM-DD" 날짜키를 "M/D" 짧은 표기로. 스파크 캡션·막대 title에 사용. */
const dayShort = (d) => {
  const [, m, day] = d.split("-");
  return `${Number(m)}/${Number(day)}`;
};

/* items를 날짜별로 묶어 {days:[정렬된 날짜키...], byDay:{날짜키: {...}}} 형태로 반환.
   dayFn(item)으로 날짜키, bucketFn(acc, item)으로 각 날짜 버킷을 누적한다.
   데이터가 실제로 존재하는 날짜만 모아 최근 maxDays개만 남긴다(빈 날짜로 납작해지는 것 방지). */
function bucketByDay(items, dayFn, bucketFn, maxDays = 8) {
  const byDay = {};
  for (const item of items) {
    const day = dayFn(item);
    byDay[day] = bucketFn(byDay[day], item);
  }
  const days = Object.keys(byDay).sort().slice(-maxDays);
  return { days, byDay };
}
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
  acctDetailOpenId = null;
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
    renderAssetSummary(accounts);
    renderAccountCards(accounts);
    fillTransferFrom(accounts);
  } catch (err) {
    document.getElementById("asset-summary").innerHTML = "";
    document.getElementById("account-cards").innerHTML =
      '<div class="card"><p>계좌 정보를 불러오지 못했습니다. 백엔드(:8000)가 실행 중인지 확인하세요.</p></div>';
    console.error("계좌 로드 실패:", err);
  }
}

function renderAssetSummary(accounts) {
  const total = accounts.reduce((sum, a) => sum + Number(a.balance), 0);
  document.getElementById("asset-summary").innerHTML = `
    <div class="asset-summary-label">총자산</div>
    <div class="asset-summary-amount">${won(total)}</div>
    <div class="asset-summary-sub">${accounts.length}개 계좌 연결됨</div>
  `;
}

function renderAccountCards(accounts) {
  document.getElementById("account-cards").innerHTML = accounts
    .map(
      (a) =>
        `<div class="card clickable acct-card" data-acct-id="${a.id}" data-acct-no="${a.account_no}">
           <div class="acct-tile-head">
             <span class="acct-bank-logo">${bankBadge(a.bank_name)}</span>
             <div class="acct-info">
               <div class="acct-bank">${escapeHtml(a.bank_name)}</div>
               <div class="acct-no">${escapeHtml(a.account_no)}</div>
             </div>
           </div>
           <div class="acct-balance">${won(a.balance)}</div>
           <div class="acct-tile-actions">
             <button type="button" class="acct-tile-action acct-tile-history">
               <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18M18 17V9M13 17V5M8 17v-3"/></svg>
               거래내역
             </button>
             <button type="button" class="acct-tile-action acct-tile-transfer" data-acct-no="${a.account_no}">
               <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 10 3 6l4-4M3 6h13a4 4 0 0 1 4 4v1M17 14l4 4-4 4M21 18H8a4 4 0 0 1-4-4v-1"/></svg>
               이체
             </button>
           </div>
         </div>`
    )
    .join("") +
    `<div class="card acct-card ghost" data-nav="mypage" data-mypage-tab="accounts">
       <span class="ghost-icon">
         <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
       </span>
       <span>계좌 추가</span>
     </div>`;
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
    toBank.innerHTML =
      `<option value="" disabled selected>은행 선택</option>` +
      TF_BANKS.map((b) => `<option value="${b}">${b}</option>`).join("");
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

// 현재 펼쳐진 계좌의 거래내역 페이지네이션 상태(페이지 번호 클릭 시 재조회에 사용)
let acctTxAccountId = null;
let acctTxAccountNo = "";
let acctTxPage = 1;

async function showTransactions(accountId, accountNo, page = 1) {
  acctTxAccountId = accountId;
  acctTxAccountNo = accountNo;
  acctTxPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(`/api/accounts/${accountId}/transactions?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}`);
    if (!res.ok) throw new Error("거래내역 조회 실패");
    const { account, transactions, total } = await res.json();
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
    renderPagination("acct-transactions-pagination", page, boMonitorTotalPages(total), "acct-tx");
    box.style.display = "block";
    // 이체 출금계좌를 클릭한 계좌로 맞춤
    document.getElementById("tf-from").value = accountNo;
  } catch (err) {
    console.error("거래내역 로드 실패:", err);
  }
}

/* 계좌 카드의 "이체" 바로가기 → 이체 탭으로 이동 + 그 계좌를 출금 계좌로 선택 */
document.addEventListener("click", (e) => {
  const transferBtn = e.target.closest(".acct-tile-transfer");
  if (!transferBtn) return;
  e.stopPropagation();
  accountGoTab("transfer");
  const fromSelect = document.getElementById("tf-from");
  if (fromSelect) {
    fromSelect.value = transferBtn.dataset.acctNo;
    updateFromBalance();
    renderTfQuickAccounts();
  }
});

/* 계좌 카드 클릭(또는 "거래내역" 버튼) → 거래내역 표시. 같은 계좌를 한 번 더
   클릭하면 접힌다(토글) */
let acctDetailOpenId = null;
document.addEventListener("click", (e) => {
  if (e.target.closest(".acct-tile-transfer")) return;
  const card = e.target.closest(".acct-card");
  if (!card) return;
  const id = card.dataset.acctId;
  const box = document.getElementById("acct-detail");
  if (acctDetailOpenId === id && box.style.display !== "none") {
    box.style.display = "none";
    acctDetailOpenId = null;
    return;
  }
  acctDetailOpenId = id;
  showTransactions(id, card.dataset.acctNo);
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
    const res = await apiFetch(`/api/accounts/lookup?account_no=${encodeURIComponent(to)}&from_account=${encodeURIComponent(from)}`);
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

/* ── 인기 통계 (상품안내 상단: 카테고리별 Top 5) ──────────────────── */
async function loadProductStats() {
  loadTopProductsGrid("stat-products");
}

/* 상품안내 카테고리 탭(#cat-tabs)과 동일한 순서 — 인기 상품 TOP5도 이 순서로 표시 */
const PRODUCT_CATEGORY_ORDER = ["예금", "적금", "주택담보대출", "전세자금대출", "신용대출"];

/* 카테고리별 Top5를 5분할(가로) 레이아웃으로 한 번에 표시(상품안내 + 백오피스 공용).
   내용은 순위+상품명, 카테고리 순서는 상품안내 탭 메뉴 순서와 동일하게 정렬. */
async function loadTopProductsGrid(targetId = "stat-products") {
  const box = document.getElementById(targetId);
  try {
    const res = await fetch("/api/stats/top-products");
    const { categories } = await res.json();
    const names = Object.keys(categories)
      .filter((c) => (categories[c] || []).length > 0)
      .sort((a, b) => PRODUCT_CATEGORY_ORDER.indexOf(a) - PRODUCT_CATEGORY_ORDER.indexOf(b));
    if (!names.length) {
      box.innerHTML = statEmpty();
      return;
    }
    const cols = names
      .map((cat) => {
        const rows = categories[cat]
          .map(
            (p, i) =>
              `<li data-category="${escapeHtml(cat)}" data-product="${escapeHtml(p.product)}">` +
              `<span class="rank">${i + 1}</span>` +
              `<span class="p-name">${escapeHtml(p.product)}</span></li>`
          )
          .join("");
        return `<div class="topcat"><div class="topcat-title">${escapeHtml(cat)}</div><ol class="topcat-list">${rows}</ol></div>`;
      })
      .join("");
    box.innerHTML = `<div class="stat-products-columns">${cols}</div>`;
  } catch (err) {
    box.innerHTML = statError();
    console.error("인기 상품 로드 실패:", err);
  }
}

document.addEventListener("click", (e) => {
  const item = e.target.closest("#stat-products .topcat-list li[data-product]");
  if (item) { goToProductInList(item.dataset.category, item.dataset.product); return; }
});

/* 인기 상품 랭킹 클릭 → 상품안내 탭 전환 후 목록에서 해당 상품을 찾아 펼쳐 보여준다.
   목록 로드가 비동기라 goto-account 메시지 처리와 동일한 폴링 방식을 쓴다. */
function goToProductInList(category, productName) {
  navigate("products");
  const tab = document.querySelector(`.cat-tab[data-category="${CSS.escape(category)}"]`);
  if (tab && !tab.classList.contains("active")) tab.click();
  let tries = 0;
  const tryFind = () => {
    if (productListAll.length > PRODUCT_LIST_PAGE_SIZE && !productListExpanded) {
      productListExpanded = true;
      renderProductList(productListAll);
    }
    const row = document.querySelector(
      `.product-list-row[data-category="${CSS.escape(category)}"][data-product="${CSS.escape(productName)}"]`
    );
    if (row) {
      row.classList.add("open");
      row.scrollIntoView({ behavior: "smooth", block: "center" });
    } else if (tries++ < 25) {
      setTimeout(tryFind, 150);
    }
  };
  tryFind();
}

function statEmpty() {
  return '<p class="stat-empty">아직 데이터가 없습니다 — 상품을 조회하거나 AI은행원에 질문해 보세요.</p>';
}
function statError() {
  return '<p class="stat-empty">통계를 불러오지 못했습니다 (백엔드 :8000 확인).</p>';
}

/* 순수 CSS 막대 랭킹 리스트. data: [{label, value, color?}]. 카테고리가 적을 땐
   도넛보다 막대가 정확한 값 비교에 유리해(dataviz 스킬 + uidesign.tips "Choose Chart
   Types Carefully") 도넛 대신 이 컴포넌트를 쓴다. opts.totalId가 있으면 총합을
   해당 엘리먼트에 "총 N건"으로 표시(도넛 홀 중앙값을 대체). */
const RANK_PALETTE = ["#0FA968", "#0B8457", "#2E86DE", "#8E7CC3", "#F2A93B", "#5F6368"];

function renderRankedBars(containerId, data, opts = {}) {
  const box = document.getElementById(containerId);
  if (!box) return;
  const total = data.reduce((s, d) => s + (d.value || 0), 0);
  if (!total) {
    box.innerHTML = statEmpty();
    if (opts.totalId) document.getElementById(opts.totalId).textContent = "";
    return;
  }

  const palette = opts.palette || RANK_PALETTE;
  const max = Math.max(...data.map((d) => d.value || 0));
  box.innerHTML = data
    .map((d, i) => {
      const color = d.color || palette[i % palette.length];
      const pct = Math.round((d.value / total) * 100);
      const width = max ? (d.value / max) * 100 : 0;
      return `<div class="rank-row">` +
        `<span class="rank-dot" style="background:${color}"></span>` +
        `<span class="rank-name">${escapeHtml(d.label)}</span>` +
        `<div class="rank-track"><div class="rank-fill" style="width:${width}%;background:${color}"></div></div>` +
        `<span class="rank-val">${d.value}건</span>` +
        `<span class="rank-pct">${pct}%</span></div>`;
    })
    .join("");

  if (opts.totalId) {
    const totalEl = document.getElementById(opts.totalId);
    if (totalEl) totalEl.textContent = `총 ${total}건`;
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

/* 카테고리 탭 클릭 → track/view(category) → FSS 상품 목록 표시. 카테고리별 인기상품 순위는
   실제 상품 클릭(아래 product-list-row 확장) 기준으로 집계하므로 여기선 product를 보내지 않는다. */
document.addEventListener("click", async (e) => {
  const tab = e.target.closest(".cat-tab");
  if (!tab) return;
  document.querySelectorAll("#cat-tabs .cat-tab").forEach((t) => t.classList.toggle("active", t === tab));
  loadProductList(tab.dataset.category, tab.dataset.desc);
  await trackView({ category: tab.dataset.category });
  loadProductStats();
});

/* FSS(금융감독원) 상품 목록 조회/렌더 */
async function loadProductList(category, desc) {
  const panel = document.getElementById("product-list-panel");
  const titleEl = document.getElementById("product-list-title");
  const descEl = document.getElementById("product-list-desc");
  const listEl = document.getElementById("product-list");
  panel.style.display = "block";
  titleEl.textContent = `${category} 상품 목록`;
  descEl.textContent = desc || "";
  listEl.innerHTML = '<p class="product-list-empty">불러오는 중…</p>';
  productSearchQuery = "";
  productSortMode = "default";
  document.getElementById("product-search").value = "";
  document.getElementById("product-sort").value = "default";
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    const res = await fetch(`/api/products?category=${encodeURIComponent(category)}`);
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({}));
      throw new Error(detail || "상품 목록을 불러오지 못했습니다.");
    }
    const { products } = await res.json();
    productListExpanded = false;
    renderProductList(products);
  } catch (err) {
    listEl.innerHTML = `<p class="product-list-empty">${escapeHtml(err.message)}</p>`;
  }
}

/* 상품 목록이 길면 상위 N개만 보여주고 "더보기/접기"로 나머지를 펼치고 접는다 */
const PRODUCT_LIST_PAGE_SIZE = 8;
let productListAll = [];
let productListExpanded = false;
let productSearchQuery = "";
let productSortMode = "default"; // default | rate-asc | rate-desc

/* 검색어(은행명/상품명)로 좁히고, 정렬 모드에 따라 재정렬한 목록 반환 */
function getFilteredProducts() {
  let list = productListAll;
  if (productSearchQuery) {
    const q = productSearchQuery;
    list = list.filter(
      (p) => p.bank.toLowerCase().includes(q) || p.product_name.toLowerCase().includes(q)
    );
  }
  if (productSortMode !== "default") {
    list = [...list].sort((a, b) => {
      const ra = getProductRateRange(a).maxRate ?? 0;
      const rb = getProductRateRange(b).maxRate ?? 0;
      return productSortMode === "rate-asc" ? ra - rb : rb - ra;
    });
  }
  return list;
}

function renderProductList(products) {
  if (products) productListAll = products;
  const filtered = getFilteredProducts();
  const listEl = document.getElementById("product-list");
  renderTopPick(getBestPick(productListAll));
  if (!productListAll.length) {
    listEl.innerHTML = '<p class="product-list-empty">조회된 상품이 없습니다.</p>';
    return;
  }
  if (!filtered.length) {
    listEl.innerHTML = '<p class="product-list-empty">검색 결과가 없습니다.</p>';
    return;
  }
  const overflow = filtered.length > PRODUCT_LIST_PAGE_SIZE;
  const visible = productListExpanded || !overflow
    ? filtered
    : filtered.slice(0, PRODUCT_LIST_PAGE_SIZE);
  const toggleBtn = overflow
    ? `<button class="product-list-toggle" type="button">${
        productListExpanded
          ? "접기 ▲"
          : `더보기 (${filtered.length - PRODUCT_LIST_PAGE_SIZE}개 더 보기) ▼`
      }</button>`
    : "";
  listEl.innerHTML = visible.map(renderProductRow).join("") + toggleBtn;
}

/* 특별상품(FSS 비연동, 백오피스 "금융상품관리 > 특별상품 관리"에서 등록) */
async function loadSpecialProducts() {
  const panel = document.getElementById("special-products-panel");
  try {
    const res = await fetch("/api/special-products?limit=20");
    const data = await res.json();
    renderSpecialProductList(data.special_products || []);
  } catch (err) {
    console.error("특별상품 로드 실패:", err);
    if (panel) panel.style.display = "none";
  }
}

function renderSpecialProductList(products) {
  const panel = document.getElementById("special-products-panel");
  const list = document.getElementById("special-product-list");
  if (!panel || !list) return;
  if (!products.length) {
    panel.style.display = "none";
    return;
  }
  panel.style.display = "";
  list.innerHTML = products
    .map((p) => {
      const badge = p.badge ? `<span class="special-badge">${escapeHtml(p.badge)}</span>` : "";
      const meta = [p.rate_text, p.description].filter(Boolean).map(escapeHtml).join(" · ");
      return `<div class="faq-item"><div class="faq-q"><span>${badge}${bankBadge(p.bank_name)} ${escapeHtml(p.title)}</span>${CHEV_SVG}</div>` +
        (meta ? `<div class="faq-a">${meta}</div>` : "") +
        `</div>`;
    })
    .join("");
}

document.getElementById("product-search").addEventListener("input", (e) => {
  productSearchQuery = e.target.value.trim().toLowerCase();
  productListExpanded = false;
  renderProductList();
});
document.getElementById("product-sort").addEventListener("change", (e) => {
  productSortMode = e.target.value;
  productListExpanded = false;
  renderProductList();
});

const LOAN_CATEGORIES = ["주택담보대출", "전세자금대출", "신용대출"];

/* 상품의 최저~최고 금리 범위 계산 (목록 행/TOP 추천 카드 공용) */
function getProductRateRange(p) {
  const isLoan = LOAN_CATEGORIES.includes(p.category);
  const rates = isLoan
    ? p.options.flatMap((o) => [o.min_rate, o.max_rate]).filter((r) => r != null)
    : p.options.map((o) => (o.max_rate != null ? o.max_rate : o.base_rate)).filter((r) => r != null);
  return {
    isLoan,
    minRate: rates.length ? Math.min(...rates) : null,
    maxRate: rates.length ? Math.max(...rates) : null,
  };
}

/* 카테고리 전체(정렬/검색 미적용) 중 실제로 가장 유리한 상품 하나를 고른다.
   대출은 최저금리, 예금/적금은 최고금리가 "좋은" 방향 — TOP추천은 사용자가
   고른 정렬/검색과 무관하게 항상 이 기준으로 고정돼야 한다. */
function getBestPick(products) {
  if (!products || !products.length) return null;
  return products.reduce((best, p) => {
    const rangeP = getProductRateRange(p);
    const valP = rangeP.isLoan ? rangeP.minRate : rangeP.maxRate;
    if (valP == null) return best;
    const rangeBest = getProductRateRange(best);
    const valBest = rangeBest.isLoan ? rangeBest.minRate : rangeBest.maxRate;
    if (valBest == null) return p;
    return rangeP.isLoan ? (valP < valBest ? p : best) : (valP > valBest ? p : best);
  }, products[0]);
}

/* 카테고리 탭 클릭 시 정렬 1순위 상품(최저금리=대출 / 최고금리=예적금)을 강조 카드로 표시 */
function renderTopPick(p) {
  const el = document.getElementById("product-top-pick");
  if (!p) { el.innerHTML = ""; return; }
  const { isLoan, minRate, maxRate } = getProductRateRange(p);
  const rateNum = isLoan ? minRate : maxRate;
  const rateLabel = isLoan ? "최저금리" : "최고금리";
  el.innerHTML = `
    <div class="top-pick">
      <span class="top-pick-badge">TOP 추천</span>
      <div class="top-pick-body">
        <div class="top-pick-bank">${bankBadge(p.bank)} ${escapeHtml(p.bank)}</div>
        <div class="top-pick-name">${escapeHtml(p.product_name)}</div>
      </div>
      <div class="top-pick-rate">
        <span class="top-pick-rate-label">${rateLabel}</span>
        <span class="top-pick-rate-num">연 ${rateNum != null ? rateNum : "-"}%</span>
      </div>
    </div>`;
}

function renderProductRow(p) {
  const { isLoan, minRate, maxRate } = getProductRateRange(p);
  /* 대출은 낮은 금리가 유리해서 최저금리를 강조(굵은글씨+초록색)하고,
     예금/적금은 반대로 최고금리를 강조한다 — 강조 클래스명(pl-rate-min/max)은
     값의 크기가 아니라 "강조 여부"를 뜻하므로 대출일 땐 서로 바꿔 붙인다. */
  const rateText =
    minRate == null
      ? "-"
      : minRate === maxRate
      ? `<span class="pl-rate-label">연</span><span class="pl-rate-max">${maxRate}%</span>`
      : isLoan
      ? `<span class="pl-rate-label">연</span><span class="pl-rate-max">${minRate}%</span><span class="pl-rate-sep">~</span><span class="pl-rate-min">${maxRate}%</span>`
      : `<span class="pl-rate-label">연</span><span class="pl-rate-min">${minRate}%</span><span class="pl-rate-sep">~</span><span class="pl-rate-max">${maxRate}%</span>`;
  const termRates = isLoan
    ? p.options
        .map((o) => {
          const rate =
            o.min_rate != null && o.max_rate != null && o.min_rate !== o.max_rate
              ? `${o.min_rate}~${o.max_rate}%`
              : `${o.min_rate ?? o.max_rate ?? o.avg_rate ?? "-"}%`;
          return `<span class="pl-term">${escapeHtml(o.rate_type || "금리")} 연 ${rate}</span>`;
        })
        .join("")
    : p.options
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
  const detailRows = isLoan
    ? [
        ["가입방법", p.join_way],
        ["대출한도", p.loan_limit],
        ["중도상환수수료", p.early_repay_fee],
      ].filter(([, v]) => v)
    : [
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
        <div class="pl-head-top">
          <span class="pl-bank">${bankBadge(p.bank)} ${escapeHtml(p.bank)}</span>
          ${catBadge}
          ${denyBadge}
        </div>
        <div class="pl-head-main">
          <span class="pl-name">${escapeHtml(p.product_name)}</span>
          <span class="pl-rate">${rateText}</span>
          <span class="chev">▾</span>
        </div>
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
  if (e.target.closest(".product-list-toggle")) {
    productListExpanded = !productListExpanded;
    renderProductList(productListAll);
    if (!productListExpanded) {
      document.getElementById("product-list-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    return;
  }
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
let banksData = []; // [{name, url}, ...] — 은행 순위 등 다른 곳에서도 이름→URL 조회용으로 재사용
async function ensureBanksLoaded() {
  if (banksLoaded) return;
  banksLoaded = true;
  try {
    const res = await fetch("data/banks.json");
    const banks = await res.json();
    banksData = banks;
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
  // 새 탭 이동은 브라우저가 처리(target=_blank), 현재 탭에서 추적만
  await trackView({ bank: chip.dataset.bank });
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

/* 회원가입 3단계 위저드 — 이체 위저드(tfGoStep)와 동일한 패턴 */
function signupGoStep(n) {
  [1, 2, 3].forEach((i) => {
    document.getElementById(`signup-step-${i}`).style.display = i === n ? "block" : "none";
  });
  document.querySelectorAll(".signup-steps .step").forEach((s) => {
    s.classList.toggle("active", Number(s.dataset.step) === n);
    s.classList.toggle("done", Number(s.dataset.step) < n);
  });
}

/* 1단계(기본 정보) 검증 통과해야 2단계로 이동 */
function validateSignupStep1() {
  const statusEl = document.getElementById("signup-step1-status");
  const pwHint = document.getElementById("signup-password-hint");
  const pw2Hint = document.getElementById("signup-password2-hint");
  statusEl.className = "tf-status";
  pwHint.className = "tf-hint"; pwHint.textContent = "4자 이상 입력하세요.";
  pw2Hint.className = "tf-hint"; pw2Hint.textContent = "";
  const username = document.getElementById("signup-username").value.trim();
  const name = document.getElementById("signup-name").value.trim();
  const password = document.getElementById("signup-password").value;
  const password2 = document.getElementById("signup-password2").value;
  const email = document.getElementById("signup-email").value.trim();
  if (!username || !name) {
    statusEl.className = "tf-status err"; statusEl.textContent = "아이디와 이름을 입력하세요.";
    return false;
  }
  if (password.length < 4) {
    pwHint.className = "tf-hint err"; pwHint.textContent = "비밀번호는 4자 이상 입력하세요.";
    return false;
  }
  if (password !== password2) {
    pw2Hint.className = "tf-hint err"; pw2Hint.textContent = "비밀번호가 일치하지 않습니다.";
    return false;
  }
  if (!EMAIL_RE.test(email)) {
    statusEl.className = "tf-status err"; statusEl.textContent = "올바른 이메일 주소를 입력하세요.";
    return false;
  }
  if (!signupPhoneVerified) {
    statusEl.className = "tf-status err"; statusEl.textContent = "휴대폰 인증을 완료해 주세요.";
    return false;
  }
  statusEl.textContent = "";
  return true;
}

document.addEventListener("click", (e) => {
  if (e.target.id === "signup-to-step2") {
    if (validateSignupStep1()) signupGoStep(2);
    return;
  }
  if (e.target.id === "signup-back-step1") {
    signupGoStep(1);
    return;
  }
  if (e.target.id === "signup-done") {
    updateAuthSectionView(); // auth 패널 상태를 정상적인 "로그인됨"으로 원복(다음 #auth 방문 시 환영 카드가 보이도록)
    navigate("home");
    return;
  }
});

function updateSignupButtonState() {
  const btn = document.getElementById("signup-submit");
  if (!btn) return;
  const terms = document.getElementById("signup-agree-terms").checked;
  const privacy = document.getElementById("signup-agree-privacy").checked;
  const openbanking = document.getElementById("signup-agree-openbanking").checked;
  const pinOk = /^\d{6}$/.test(document.getElementById("signup-transfer-pw").value);
  btn.disabled = !(terms && privacy && openbanking && pinOk && signupPhoneVerified && signupAccountVerified);
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
    hint.textContent = "인증번호 123456 이(가) 발송되었습니다.";
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
  if (id === "signup-transfer-pw") updateSignupButtonState();
});

document.addEventListener("submit", (e) => {
  if (e.target.id === "login-form") { e.preventDefault(); handleLogin(); }
  if (e.target.id === "signup-form") { e.preventDefault(); handleSignup(); }
  if (e.target.id === "findid-form") { e.preventDefault(); handleFindId(); }
  if (e.target.id === "resetpw-form") { e.preventDefault(); handleResetPw(); }
});

document.addEventListener("click", (e) => {
  const socialBtn = e.target.closest("[data-social]");
  if (!socialBtn) return;
  const label = { kakao: "카카오", naver: "네이버", google: "Google" }[socialBtn.dataset.social];
  const statusEl = document.getElementById("login-status");
  if (statusEl) {
    statusEl.className = "tf-status";
    statusEl.textContent = `${label} 간편로그인은 준비 중입니다.`;
  }
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
  const transferPw = document.getElementById("signup-transfer-pw").value;
  if (!/^\d{6}$/.test(transferPw)) {
    statusEl.className = "tf-status err";
    statusEl.textContent = "이체 비밀번호는 숫자 6자리로 입력해 주세요.";
    return;
  }
  const payload = {
    username, password, name,
    transfer_password: transferPw,
    phone: document.getElementById("signup-phone").value.replace(/[^0-9]/g, ""),
    email: document.getElementById("signup-email").value.trim(),
    bank_name: document.getElementById("signup-bank").value,
    account_no: document.getElementById("signup-account-no").value.trim(),
    account_holder: document.getElementById("signup-account-holder").value.trim(),
    nickname: document.getElementById("signup-account-nickname").value.trim(),
    is_primary: document.getElementById("signup-account-primary").checked,
    agree_openbanking: document.getElementById("signup-agree-openbanking").checked,
    agree_marketing: document.getElementById("signup-agree-marketing").checked,
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
    refreshAuthUI(); // 로그인 상태로 전환되면서 updateAuthSectionView()가 signup-panel을 숨기므로,
    document.getElementById("signup-panel").style.display = "";      // 3단계 완료 화면을 보여주기 위해 다시 펼치고
    document.getElementById("auth-welcome").style.display = "none";  // 환영 카드는 잠시 숨긴다
    signupGoStep(3);
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

/* ── 로그아웃 상태: 아이디 찾기 / 비밀번호 재설정 ──────────────────── */
/* 아이디 찾기/비밀번호 찾기 인증(데모 시뮬레이션) — 회원가입 인증과 동일한 고정 코드 방식.
   지금은 이메일 인증만 지원(문자 인증은 추후 추가) */
let findIdOtpVerified = false, findIdOtpExpected = "";
let resetPwOtpVerified = false, resetPwOtpExpected = "";
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function updateFindIdButtonState() {
  const btn = document.getElementById("findid-submit");
  if (btn) btn.disabled = !findIdOtpVerified;
}
/* STEP 1 "다음" — 아이디찾기와 동일하게 이메일 인증 완료 여부로만 활성화 */
function updateResetPwStep1ButtonState() {
  const btn = document.getElementById("resetpw-to-step2");
  if (btn) btn.disabled = !resetPwOtpVerified;
}
/* STEP 2 "비밀번호 재설정" — 인증 완료 + 새 비밀번호 두 칸이 모두 채워지고(4자 이상) 일치할 때만 활성화 */
function updateResetPwButtonState() {
  const btn = document.getElementById("resetpw-submit");
  if (!btn) return;
  const pw = document.getElementById("resetpw-new").value;
  const pw2 = document.getElementById("resetpw-new2").value;
  btn.disabled = !(resetPwOtpVerified && pw.length >= 4 && pw === pw2);
}
function resetFindIdVerification() {
  findIdOtpVerified = false; findIdOtpExpected = "";
  const row = document.getElementById("findid-otp-code-row");
  if (row) row.style.display = "none";
  const hint = document.getElementById("findid-otp-hint");
  if (hint) { hint.className = "tf-hint"; hint.textContent = ""; }
  updateFindIdButtonState();
}
function resetResetPwVerification() {
  resetPwOtpVerified = false; resetPwOtpExpected = "";
  const row = document.getElementById("resetpw-otp-code-row");
  if (row) row.style.display = "none";
  const hint = document.getElementById("resetpw-otp-hint");
  if (hint) { hint.className = "tf-hint"; hint.textContent = ""; }
  updateResetPwStep1ButtonState();
  updateResetPwButtonState();
}

document.addEventListener("click", (e) => {
  if (e.target.id === "findid-otp-send") {
    const name = document.getElementById("findid-name").value.trim();
    const email = document.getElementById("findid-email").value.trim();
    const hint = document.getElementById("findid-otp-hint");
    if (!name) {
      hint.className = "tf-status err"; hint.textContent = "이름을 입력하세요.";
      return;
    }
    if (!EMAIL_RE.test(email)) {
      hint.className = "tf-status err"; hint.textContent = "올바른 이메일 주소를 입력하세요.";
      return;
    }
    findIdOtpExpected = "123456";
    document.getElementById("findid-otp-code-row").style.display = "";
    hint.className = "tf-hint";
    hint.textContent = `${email} 로 인증번호가 발송되었습니다.`;
    return;
  }
  if (e.target.id === "findid-otp-confirm") {
    const code = document.getElementById("findid-otp-code").value.trim();
    const hint = document.getElementById("findid-otp-hint");
    if (findIdOtpExpected && code === findIdOtpExpected) {
      findIdOtpVerified = true;
      hint.className = "tf-hint"; hint.innerHTML = `<span class="verify-badge ok">✓ 인증 완료</span>`;
    } else {
      findIdOtpVerified = false;
      hint.className = "tf-status err"; hint.textContent = "인증번호가 일치하지 않습니다.";
    }
    updateFindIdButtonState();
    return;
  }

  if (e.target.id === "resetpw-otp-send") {
    const username = document.getElementById("resetpw-username").value.trim();
    const name = document.getElementById("resetpw-name").value.trim();
    const phone = document.getElementById("resetpw-phone").value.replace(/[^0-9]/g, "");
    const email = document.getElementById("resetpw-email").value.trim();
    const hint = document.getElementById("resetpw-otp-hint");
    if (!username || !name || phone.length < 10) {
      hint.className = "tf-status err"; hint.textContent = "아이디·이름·전화번호를 정확히 입력하세요.";
      return;
    }
    if (!EMAIL_RE.test(email)) {
      hint.className = "tf-status err"; hint.textContent = "올바른 이메일 주소를 입력하세요.";
      return;
    }
    resetPwOtpExpected = "123456";
    document.getElementById("resetpw-otp-code-row").style.display = "";
    hint.className = "tf-hint";
    hint.textContent = `${email} 로 인증번호가 발송되었습니다.`;
    return;
  }
  if (e.target.id === "resetpw-otp-confirm") {
    const code = document.getElementById("resetpw-otp-code").value.trim();
    const hint = document.getElementById("resetpw-otp-hint");
    if (resetPwOtpExpected && code === resetPwOtpExpected) {
      resetPwOtpVerified = true;
      hint.className = "tf-hint"; hint.innerHTML = `<span class="verify-badge ok">✓ 인증 완료</span>`;
    } else {
      resetPwOtpVerified = false;
      hint.className = "tf-status err"; hint.textContent = "인증번호가 일치하지 않습니다.";
    }
    updateResetPwStep1ButtonState();
    updateResetPwButtonState();
    return;
  }
});

async function handleFindId() {
  const statusEl = document.getElementById("findid-status");
  statusEl.className = "tf-status";
  statusEl.textContent = "조회 중…";
  try {
    const res = await fetch("/api/find-username", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: document.getElementById("findid-name").value.trim(),
        email: document.getElementById("findid-email").value.trim(),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "조회에 실패했습니다.");
    statusEl.className = "tf-status ok";
    statusEl.textContent = `회원님의 아이디는 "${data.username_masked}" 입니다.`;
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
}

/* 비밀번호 찾기 2단계 위저드 — signupGoStep과 동일한 패턴 */
/* 새 비밀번호 입력 중에도 실시간으로 CTA 활성화 상태를 갱신 */
document.addEventListener("input", (e) => {
  if (e.target.id === "resetpw-new" || e.target.id === "resetpw-new2") updateResetPwButtonState();
});

function resetpwGoStep(n) {
  [1, 2].forEach((i) => {
    document.getElementById(`resetpw-step-${i}`).style.display = i === n ? "block" : "none";
  });
  document.querySelectorAll(".resetpw-steps .step").forEach((s) => {
    s.classList.toggle("active", Number(s.dataset.step) === n);
    s.classList.toggle("done", Number(s.dataset.step) < n);
  });
}

function validateResetPwStep1() {
  const statusEl = document.getElementById("resetpw-step1-status");
  statusEl.className = "tf-status";
  const username = document.getElementById("resetpw-username").value.trim();
  const name = document.getElementById("resetpw-name").value.trim();
  const phone = document.getElementById("resetpw-phone").value.replace(/[^0-9]/g, "");
  if (!username || !name || phone.length < 10) {
    statusEl.className = "tf-status err"; statusEl.textContent = "아이디·이름·전화번호를 정확히 입력하세요.";
    return false;
  }
  if (!resetPwOtpVerified) {
    statusEl.className = "tf-status err"; statusEl.textContent = "이메일 인증을 완료해 주세요.";
    return false;
  }
  statusEl.textContent = "";
  return true;
}

document.addEventListener("click", (e) => {
  if (e.target.id === "resetpw-to-step2") {
    if (validateResetPwStep1()) resetpwGoStep(2);
    return;
  }
  if (e.target.id === "resetpw-back-step1") {
    resetpwGoStep(1);
    return;
  }
});

async function handleResetPw() {
  const statusEl = document.getElementById("resetpw-status");
  const pwHint = document.getElementById("resetpw-new-hint");
  const pw2Hint = document.getElementById("resetpw-new2-hint");
  statusEl.className = "tf-status";
  pwHint.className = "tf-hint"; pwHint.textContent = "4자 이상 입력하세요.";
  pw2Hint.className = "tf-hint"; pw2Hint.textContent = "";
  const newPassword = document.getElementById("resetpw-new").value;
  const newPassword2 = document.getElementById("resetpw-new2").value;
  if (newPassword.length < 4) {
    pwHint.className = "tf-hint err"; pwHint.textContent = "비밀번호는 4자 이상 입력하세요.";
    return;
  }
  if (newPassword !== newPassword2) {
    pw2Hint.className = "tf-hint err"; pw2Hint.textContent = "비밀번호가 일치하지 않습니다.";
    return;
  }
  statusEl.textContent = "처리 중…";
  try {
    const res = await fetch("/api/reset-password", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.getElementById("resetpw-username").value.trim(),
        name: document.getElementById("resetpw-name").value.trim(),
        phone: document.getElementById("resetpw-phone").value.replace(/[^0-9]/g, ""),
        new_password: document.getElementById("resetpw-new").value,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "재설정에 실패했습니다.");
    document.getElementById("resetpw-form").reset();
    resetResetPwVerification();
    statusEl.className = "tf-status ok";
    statusEl.textContent = "비밀번호가 재설정되었습니다. 새 비밀번호로 로그인하세요.";
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
}

/* ── 마이페이지 ─────────────────────────────────────────────────────── */
const MYPAGE_TABS = ["profile", "security", "accounts", "favorites",
                     "scheduled", "inquiries", "statement", "withdraw"];

function mypageGoTab(name) {
  if (!MYPAGE_TABS.includes(name)) name = "profile";
  document.querySelectorAll(".mypage-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.mypageTab === name)
  );
  MYPAGE_TABS.forEach((t) => {
    document.getElementById(`mypage-panel-${t}`).style.display = t === name ? "block" : "none";
  });
  if (name === "profile") loadMyProfile();
  if (name === "security") { loadSecurityOverview(); loadMyTransferPwState(); loadMyConsents(); loadMyLimits(); loadMySecurityEvents(); }
  if (name === "accounts") loadMyAccounts();
  if (name === "favorites") loadMyFavorites();
  if (name === "scheduled") loadMyScheduled();
  if (name === "inquiries") loadMyInquiries();
  if (name === "statement") loadMyStatementList();
}

document.addEventListener("click", (e) => {
  const tab = e.target.closest(".mypage-tab");
  if (tab) mypageGoTab(tab.dataset.mypageTab);
});

const mpFmtDate = (ts) => new Date(ts * 1000).toLocaleString("ko-KR");
function mpStatus(id, msg, ok) {
  const el = document.getElementById(id);
  el.className = "tf-status" + (ok ? " ok" : msg ? " err" : "");
  el.textContent = msg;
}
function fillBankOptions(id) {
  const sel = document.getElementById(id);
  if (sel && !sel.options.length) {
    sel.innerHTML =
      `<option value="" disabled selected>은행 선택</option>` +
      TF_BANKS.map((b) => `<option value="${b}">${b}</option>`).join("");
  }
}

/* 내 정보 */
async function loadMyProfile() {
  try {
    const res = await apiFetch("/api/me/profile");
    if (!res.ok) throw new Error("프로필 조회 실패");
    const p = await res.json();
    document.getElementById("mp-username").value = p.username;
    document.getElementById("mp-name").value = p.name || "";
    document.getElementById("mp-phone").value = p.phone || "";
    document.getElementById("mp-email").value = p.email || "";
    document.getElementById("mp-profile-avatar").textContent = (p.name || p.username || "?").trim().charAt(0);
    document.getElementById("mp-profile-name").textContent = p.name || p.username;
    document.getElementById("mp-profile-username").textContent = "@" + p.username;
    document.getElementById("mp-profile-joined").textContent = p.created_at
      ? new Date(p.created_at * 1000).toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric" }) + " 가입"
      : "";
  } catch (err) { mpStatus("mp-profile-status", err.message); }
}
async function saveMyProfile() {
  mpStatus("mp-profile-status", "저장 중…");
  try {
    const res = await apiFetch("/api/me/profile", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: document.getElementById("mp-name").value.trim(),
        phone: document.getElementById("mp-phone").value.replace(/[^0-9]/g, ""),
        email: document.getElementById("mp-email").value.trim(),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "저장 실패");
    mpStatus("mp-profile-status", "저장되었습니다.", true);
  } catch (err) { mpStatus("mp-profile-status", err.message); }
}

/* 로그인 비밀번호 변경 */
async function changeMyPassword() {
  mpStatus("mp-password-status", "변경 중…");
  try {
    const res = await apiFetch("/api/me/password", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password: document.getElementById("mp-cur-pw").value,
        new_password: document.getElementById("mp-new-pw").value,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "변경 실패");
    document.getElementById("mp-password-form").reset();
    mpStatus("mp-password-status", "비밀번호가 변경되었습니다.", true);
  } catch (err) { mpStatus("mp-password-status", err.message); }
}

/* 보안 탭 상단: 이체 비밀번호·오픈뱅킹 동의·최근 보안 이벤트를 한눈에 보여주는 요약 카드 */
async function loadSecurityOverview() {
  const box = document.getElementById("mp-security-overview");
  try {
    const [profileRes, consentsRes, eventsRes] = await Promise.all([
      apiFetch("/api/me/profile"),
      apiFetch("/api/me/consents"),
      apiFetch("/api/me/security-events"),
    ]);
    const profile = profileRes.ok ? await profileRes.json() : {};
    const consents = consentsRes.ok ? await consentsRes.json() : {};
    const { events } = eventsRes.ok ? await eventsRes.json() : { events: [] };
    const tpwOk = !!profile.has_transfer_password;
    const obOk = !!consents.agree_openbanking;
    const evCount = events.length;
    box.innerHTML = `
      <div class="mp-sec-stat">
        <span class="mp-sec-stat-badge ${tpwOk ? "ok" : "warn"}">${tpwOk ? "설정됨" : "미설정"}</span>
        <span class="mp-sec-stat-label">이체 비밀번호</span>
      </div>
      <div class="mp-sec-stat">
        <span class="mp-sec-stat-badge ${obOk ? "ok" : "warn"}">${obOk ? "동의완료" : "미동의"}</span>
        <span class="mp-sec-stat-label">오픈뱅킹 동의</span>
      </div>
      <div class="mp-sec-stat">
        <span class="mp-sec-stat-badge ${evCount > 0 ? "warn" : "ok"}">${evCount}건</span>
        <span class="mp-sec-stat-label">보안 이벤트</span>
      </div>`;
  } catch { box.innerHTML = ""; }
}

/* 이체 비밀번호 설정/변경 */
async function loadMyTransferPwState() {
  try {
    const res = await apiFetch("/api/me/profile");
    if (!res.ok) return;
    const p = await res.json();
    const el = document.getElementById("mp-tpw-state");
    el.textContent = p.has_transfer_password ? "설정됨" : "미설정";
    el.className = `mp-tpw-badge ${p.has_transfer_password ? "ok" : "warn"}`;
  } catch { /* noop */ }
}
async function changeMyTransferPw() {
  const pin = document.getElementById("mp-tpw-new").value;
  if (!/^\d{6}$/.test(pin)) { mpStatus("mp-tpw-status", "이체 비밀번호는 숫자 6자리입니다."); return; }
  mpStatus("mp-tpw-status", "저장 중…");
  try {
    const res = await apiFetch("/api/me/transfer-password", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        login_password: document.getElementById("mp-tpw-login").value,
        new_transfer_password: pin,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "저장 실패");
    document.getElementById("mp-tpw-form").reset();
    loadMyTransferPwState();
    mpStatus("mp-tpw-status", "이체 비밀번호가 저장되었습니다.", true);
  } catch (err) { mpStatus("mp-tpw-status", err.message); }
}

/* 알림 동의 */
async function loadMyConsents() {
  try {
    const res = await apiFetch("/api/me/consents");
    if (!res.ok) return;
    const c = await res.json();
    document.getElementById("mp-agree-openbanking").checked = c.agree_openbanking;
    document.getElementById("mp-agree-marketing").checked = c.agree_marketing;
  } catch { /* noop */ }
}
async function saveMyConsents() {
  mpStatus("mp-consent-status", "저장 중…");
  try {
    const res = await apiFetch("/api/me/consents", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        agree_openbanking: document.getElementById("mp-agree-openbanking").checked,
        agree_marketing: document.getElementById("mp-agree-marketing").checked,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "저장 실패");
    mpStatus("mp-consent-status", "저장되었습니다.", true);
  } catch (err) { mpStatus("mp-consent-status", err.message); }
}

/* 내 이체 한도(읽기전용) */
async function loadMyLimits() {
  const box = document.getElementById("mp-limits");
  try {
    const res = await apiFetch("/api/me/limits");
    if (!res.ok) throw new Error("한도 조회 실패");
    const l = await res.json();
    box.innerHTML =
      `<div class="cf-row"><span>1회 한도</span><b>${won(l.transfer_limit)}</b></div>` +
      `<div class="cf-row"><span>1일 한도</span><b>${won(l.daily_transfer_limit)}</b></div>` +
      `<div class="cf-row"><span>타행 수수료</span><b>${won(l.transfer_fee)}</b></div>` +
      `<div class="cf-row"><span>오늘 사용액</span><b>${won(l.used_today)}</b></div>` +
      `<div class="cf-row"><span>오늘 잔여 한도</span><b>${won(l.remaining_today)}</b></div>`;
  } catch (err) { box.innerHTML = `<p class="tf-hint">${escapeHtml(err.message)}</p>`; }
}

/* 내 보안 이력 */
const SEC_LABELS = { password_fail: "이체 비밀번호 오류", limit_once: "1회 한도 초과",
                     limit_daily: "1일 한도 초과", new_payee: "신규 수취계좌 이체" };
async function loadMySecurityEvents() {
  const box = document.getElementById("mp-security-events");
  try {
    const res = await apiFetch("/api/me/security-events");
    if (!res.ok) throw new Error("보안 이력 조회 실패");
    const { events } = await res.json();
    if (!events.length) { box.innerHTML = `<p class="tf-hint">보안 이벤트가 없습니다.</p>`; return; }
    box.innerHTML = events.map((ev) =>
      `<div class="mp-sec-item" data-severity="${ev.event_type === "new_payee" ? "info" : "warn"}"><span class="mp-sec-dot"></span>` +
      `<span class="mp-sec-body"><span class="mp-sec-type">${escapeHtml(SEC_LABELS[ev.event_type] || ev.event_type)}</span>` +
      `<span class="mp-sec-detail">${escapeHtml(ev.detail || "")}</span></span>` +
      `<span class="mp-sec-time">${mpFmtDate(ev.created_at)}</span></div>`
    ).join("");
  } catch (err) { box.innerHTML = `<p class="tf-hint">${escapeHtml(err.message)}</p>`; }
}

/* 계좌 관리 */
async function loadMyAccounts() {
  fillBankOptions("mp-acc-bank");
  // 예금주명은 반드시 회원 이름과 일치해야 하므로 항상 현재 사용자 이름으로 채운다.
  const holderEl = document.getElementById("mp-acc-holder");
  if (holderEl) holderEl.value = getAuth()?.name || "";
  const box = document.getElementById("mp-accounts");
  try {
    const res = await apiFetch("/api/accounts");
    if (!res.ok) throw new Error("계좌 조회 실패");
    const { accounts } = await res.json();
    box.innerHTML = accounts.map((a) =>
      `<div class="mp-acct" data-acc-id="${a.id}">
         <div class="mp-acct-head">
           <span class="mp-acct-logo">${bankBadge(a.bank_name)}</span>
           <div class="mp-acct-titles">
             <div class="mp-acct-bank">${escapeHtml(a.bank_name)} ${a.is_primary ? '<span class="mp-primary-badge">대표</span>' : ""}</div>
             <div class="mp-acct-no">${escapeHtml(a.account_no)}</div>
           </div>
         </div>
         <div class="mp-acct-bal">${won(a.balance)}</div>
         <div class="mp-acct-actions">
           <input type="text" class="mp-acct-nick" value="${escapeHtml(a.nickname || "")}" placeholder="별칭" maxlength="20" />
           <button type="button" class="btn btn-ghost mp-acct-save" data-acc-id="${a.id}">별칭 저장</button>
           ${a.is_primary ? "" : `<button type="button" class="btn btn-ghost mp-acct-primary" data-acc-id="${a.id}">대표 지정</button>`}
           ${a.is_primary ? "" : `<button type="button" class="btn btn-ghost mp-acct-del" data-acc-id="${a.id}">해지</button>`}
         </div>
       </div>`
    ).join("");
  } catch (err) { box.innerHTML = `<p class="tf-hint">${escapeHtml(err.message)}</p>`; }
}
async function addMyAccount() {
  mpStatus("mp-add-account-status", "추가 중…");
  try {
    const res = await apiFetch("/api/accounts", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bank_name: document.getElementById("mp-acc-bank").value,
        account_no: document.getElementById("mp-acc-no").value.trim(),
        account_holder: document.getElementById("mp-acc-holder").value.trim(),
        nickname: document.getElementById("mp-acc-nickname").value.trim(),
        is_primary: document.getElementById("mp-acc-primary").checked,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "추가 실패");
    document.getElementById("mp-acc-no").value = "";
    document.getElementById("mp-acc-nickname").value = "";
    document.getElementById("mp-acc-primary").checked = false;
    mpStatus("mp-add-account-status", "계좌가 추가되었습니다.", true);
    loadMyAccounts();
  } catch (err) { mpStatus("mp-add-account-status", err.message); }
}

/* 즐겨찾기 */
async function loadMyFavorites() {
  fillBankOptions("mp-fav-bank");
  const box = document.getElementById("mp-favorites");
  try {
    const res = await apiFetch("/api/me/favorites");
    if (!res.ok) throw new Error("즐겨찾기 조회 실패");
    const { favorites } = await res.json();
    if (!favorites.length) { box.innerHTML = `<p class="mp-empty">등록된 즐겨찾기가 없습니다.</p>`; return; }
    box.innerHTML = favorites.map((f) =>
      `<div class="mp-fav">
         <div class="mp-fav-info">
           <span class="mp-fav-name">${escapeHtml(f.nickname || f.holder_name || "-")}</span>
           <span class="mp-fav-sub">${escapeHtml(f.bank_name)} ${escapeHtml(f.account_no)}</span>
         </div>
         <button type="button" class="btn btn-ghost mp-fav-del" data-fav-id="${f.id}">삭제</button>
       </div>`
    ).join("");
  } catch (err) { box.innerHTML = `<p class="tf-hint">${escapeHtml(err.message)}</p>`; }
}
async function addMyFavorite() {
  mpStatus("mp-add-fav-status", "추가 중…");
  try {
    const res = await apiFetch("/api/me/favorites", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bank_name: document.getElementById("mp-fav-bank").value,
        account_no: document.getElementById("mp-fav-no").value.trim(),
        holder_name: document.getElementById("mp-fav-holder").value.trim(),
        nickname: document.getElementById("mp-fav-nickname").value.trim(),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "추가 실패");
    document.getElementById("mp-add-fav-form").reset();
    mpStatus("mp-add-fav-status", "추가되었습니다.", true);
    loadMyFavorites();
  } catch (err) { mpStatus("mp-add-fav-status", err.message); }
}

/* 예약/지연 이체 */
async function loadMyScheduled() {
  const box = document.getElementById("mp-scheduled");
  try {
    const res = await apiFetch("/api/me/scheduled-transfers");
    if (!res.ok) throw new Error("예약이체 조회 실패");
    const { transfers } = await res.json();
    if (!transfers.length) { box.innerHTML = `<p class="mp-empty">대기 중인 예약/지연 이체가 없습니다.</p>`; return; }
    box.innerHTML = transfers.map((t) =>
      `<div class="mp-sched">
         <span class="mp-sched-badge ${t.status === "scheduled" ? "info" : "warn"}">${t.status === "scheduled" ? "예약" : "지연"}</span>
         <div class="mp-sched-info">
           <span class="mp-sched-to">${escapeHtml(t.to_holder || "")} ${escapeHtml(t.to_account)}</span>
           <span class="mp-sched-time">${t.scheduled_at ? mpFmtDate(t.scheduled_at) : ""}</span>
         </div>
         <span class="mp-sched-amt">${won(t.amount)}</span>
         <button type="button" class="btn btn-ghost mp-sched-cancel" data-tid="${t.id}">취소</button>
       </div>`
    ).join("");
  } catch (err) { box.innerHTML = `<p class="tf-hint">${escapeHtml(err.message)}</p>`; }
}

/* 내 문의 내역 */
let mpInquiryPage = 1;

async function loadMyInquiries(page = 1) {
  mpInquiryPage = page;
  const box = document.getElementById("mp-inquiries");
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(`/api/inquiries?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}`);
    if (!res.ok) throw new Error("문의 내역 조회 실패");
    const { inquiries, total } = await res.json();
    if (!inquiries.length) {
      box.innerHTML = `<p class="mp-empty">문의 내역이 없습니다.</p>`;
      renderPagination("mp-inquiries-pagination", 1, 1, "my-inquiries");
      return;
    }
    box.innerHTML = inquiries.map((q) =>
      `<div class="mp-inquiry">
         <div class="mp-inquiry-head"><b>${escapeHtml(q.title)}</b><span class="mp-sec-time">${mpFmtDate(q.created_at)}</span></div>
         <div class="mp-inquiry-body">${escapeHtml(q.content)}</div>
       </div>`
    ).join("");
    renderPagination("mp-inquiries-pagination", page, boMonitorTotalPages(total), "my-inquiries");
  } catch (err) { box.innerHTML = `<p class="tf-hint">${escapeHtml(err.message)}</p>`; }
}

/* 거래명세서: 전 계좌 거래내역을 모아 체크박스 목록으로 표시.
   목록 자체는 서버 페이지네이션 없이 한 번에 다 불러온 뒤(mpStatementTx) 화면에는 30건씩만
   잘라 보여준다(클라이언트 페이지네이션). 선택 상태는 DOM(:checked)이 아니라 원본 배열 인덱스
   기준 Set(mpStmtSelected)으로 따로 추적한다 — 페이지를 넘기면 이전 페이지 체크박스는 DOM에서
   사라지므로 querySelectorAll(":checked")로는 페이지를 넘나드는 선택을 유지할 수 없다. */
let mpStatementTx = [];
let mpStmtSelected = new Set();
let mpStmtPage = 1;

async function loadMyStatementList() {
  const box = document.getElementById("mp-stmt-list");
  box.innerHTML = '<p class="tf-hint">불러오는 중…</p>';
  try {
    const accRes = await apiFetch("/api/accounts");
    if (!accRes.ok) throw new Error("계좌 조회 실패");
    const { accounts } = await accRes.json();
    const perAccount = await Promise.all(
      accounts.map(async (a) => {
        const res = await apiFetch(`/api/accounts/${a.id}/transactions`);
        if (!res.ok) return [];
        const { transactions } = await res.json();
        return transactions.map((t) => ({ ...t, bank_name: a.bank_name, account_no: a.account_no }));
      })
    );
    mpStatementTx = perAccount.flat().sort((x, y) => y.created_at - x.created_at);
    mpStmtSelected = new Set(mpStatementTx.map((_, i) => i));   // 기본값: 전체 선택
    renderMyStatementList(1);
  } catch (err) {
    box.innerHTML = `<p class="tf-hint">${escapeHtml(err.message)}</p>`;
  }
}

function renderMyStatementList(page = 1) {
  mpStmtPage = page;
  const box = document.getElementById("mp-stmt-list");
  if (!mpStatementTx.length) {
    box.innerHTML = '<p class="mp-empty">거래내역이 없습니다.</p>';
    renderPagination("mp-stmt-pagination", 1, 1, "my-statement");
    updateStmtSelectCount();
    return;
  }
  const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
  box.innerHTML = mpStatementTx
    .slice(offset, offset + BO_MONITOR_PAGE_SIZE)
    .map((t, j) => {
      const i = offset + j;
      const inflow = t.type === "in";
      const sign = inflow ? "+" : "−";
      const cls = inflow ? "tx-in" : "tx-out";
      const d = new Date(t.created_at * 1000).toLocaleDateString("ko-KR");
      return `<label class="mp-stmt-row">
          <input type="checkbox" class="mp-stmt-check" data-idx="${i}" ${mpStmtSelected.has(i) ? "checked" : ""} />
          <span class="mp-stmt-date">${d}</span>
          <span class="mp-stmt-bank">${escapeHtml(t.bank_name)}</span>
          <span class="mp-stmt-cp">${escapeHtml(t.counterparty || "-")}</span>
          <span class="mp-stmt-amt ${cls}">${sign}${won(t.amount)}</span>
        </label>`;
    })
    .join("");
  renderPagination("mp-stmt-pagination", page, boMonitorTotalPages(mpStatementTx.length), "my-statement");
  updateStmtSelectCount();
}

function updateStmtSelectCount() {
  const total = mpStatementTx.length;
  const checked = mpStmtSelected.size;
  const label = document.getElementById("mp-stmt-select-count");
  const selectAll = document.getElementById("mp-stmt-select-all");
  if (!label) return;
  label.textContent = total ? `${checked}/${total}건 선택` : "전체 선택";
  if (selectAll) selectAll.checked = total > 0 && checked === total;
}

document.addEventListener("change", (e) => {
  if (e.target.id === "mp-stmt-select-all") {
    if (e.target.checked) mpStmtSelected = new Set(mpStatementTx.map((_, i) => i));
    else mpStmtSelected.clear();
    document.querySelectorAll(".mp-stmt-check").forEach((c) => (c.checked = e.target.checked));
    updateStmtSelectCount();
  }
  if (e.target.classList.contains("mp-stmt-check")) {
    const idx = Number(e.target.dataset.idx);
    if (e.target.checked) mpStmtSelected.add(idx);
    else mpStmtSelected.delete(idx);
    updateStmtSelectCount();
  }
});

/* 선택한 거래내역만 인쇄 전용 영역에 채운 뒤 브라우저 인쇄(→PDF 저장)로 출력.
   한글 폰트를 임베드해야 하는 클라이언트 PDF 라이브러리 대신, 이미 렌더링되는
   브라우저 폰트를 그대로 쓸 수 있는 window.print() 방식을 택했다. */
function printMyStatementPdf() {
  const idxs = [...mpStmtSelected].sort((a, b) => a - b);
  if (!idxs.length) { mpStatus("mp-statement-status", "다운로드할 거래내역을 선택하세요."); return; }
  const rows = idxs.map((i) => mpStatementTx[i]);
  const today = new Date().toLocaleDateString("ko-KR");
  const rowsHtml = rows
    .map((t) => {
      const inflow = t.type === "in";
      const d = new Date(t.created_at * 1000).toLocaleString("ko-KR");
      return `<tr>
          <td>${escapeHtml(d)}</td>
          <td>${escapeHtml(t.bank_name)} ${escapeHtml(t.account_no)}</td>
          <td>${inflow ? "입금" : "출금"}</td>
          <td>${escapeHtml(t.counterparty || "-")}</td>
          <td class="num">${won(t.amount)}</td>
          <td class="num">${t.balance_after == null ? "-" : won(t.balance_after)}</td>
        </tr>`;
    })
    .join("");
  document.getElementById("mp-stmt-print-area").innerHTML = `
    <h1>매치뱅크 거래명세서</h1>
    <p>발급일: ${today} · 총 ${rows.length}건</p>
    <table>
      <thead><tr><th>거래일시</th><th>계좌</th><th>구분</th><th>상대</th><th>금액</th><th>거래후잔액</th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>`;
  window.print();
  mpStatus("mp-statement-status", `${rows.length}건으로 인쇄 대화상자를 열었습니다. "PDF로 저장"을 선택하세요.`, true);
}

/* 회원 탈퇴 */
async function withdrawMe() {
  mpStatus("mp-withdraw-status", "");
  const pw = document.getElementById("mp-withdraw-pw").value;
  if (!pw) { mpStatus("mp-withdraw-status", "비밀번호를 입력하세요."); return; }
  if (!window.confirm("정말 탈퇴하시겠습니까? 탈퇴 후에는 로그인할 수 없습니다.")) return;
  try {
    const res = await apiFetch("/api/me/withdraw", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "탈퇴 실패");
    alert("회원 탈퇴가 완료되었습니다.");
    clearAuth();
    refreshAuthUI();
    navigate("home");
  } catch (err) { mpStatus("mp-withdraw-status", err.message); }
}

/* 마이페이지 폼 submit 위임 */
document.addEventListener("submit", (e) => {
  const id = e.target.id;
  if (id === "mp-profile-form") { e.preventDefault(); saveMyProfile(); }
  if (id === "mp-password-form") { e.preventDefault(); changeMyPassword(); }
  if (id === "mp-tpw-form") { e.preventDefault(); changeMyTransferPw(); }
  if (id === "mp-consent-form") { e.preventDefault(); saveMyConsents(); }
  if (id === "mp-add-account-form") { e.preventDefault(); addMyAccount(); }
  if (id === "mp-add-fav-form") { e.preventDefault(); addMyFavorite(); }
  if (id === "mp-withdraw-form") { e.preventDefault(); withdrawMe(); }
});

/* 마이페이지 클릭 위임 (계좌/즐겨찾기/예약이체 액션) */
document.addEventListener("click", async (e) => {
  const t = e.target;
  if (t.closest("#mp-download-pdf")) { printMyStatementPdf(); return; }

  const save = t.closest(".mp-acct-save");
  if (save) {
    const row = save.closest(".mp-acct");
    const nick = row.querySelector(".mp-acct-nick").value.trim();
    const res = await apiFetch(`/api/accounts/${save.dataset.accId}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nickname: nick }),
    });
    if (res.ok) loadMyAccounts();
    return;
  }
  const prim = t.closest(".mp-acct-primary");
  if (prim) {
    const res = await apiFetch(`/api/accounts/${prim.dataset.accId}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_primary: true }),
    });
    if (res.ok) loadMyAccounts();
    return;
  }
  const del = t.closest(".mp-acct-del");
  if (del) {
    if (!window.confirm("이 계좌를 해지하시겠습니까?")) return;
    const res = await apiFetch(`/api/accounts/${del.dataset.accId}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { alert(data.detail || "해지할 수 없습니다."); return; }
    loadMyAccounts();
    return;
  }
  const favDel = t.closest(".mp-fav-del");
  if (favDel) {
    if (!window.confirm("이 즐겨찾기를 삭제하시겠습니까?")) return;
    const res = await apiFetch(`/api/me/favorites/${favDel.dataset.favId}`, { method: "DELETE" });
    if (res.ok) loadMyFavorites();
    return;
  }
  const cancel = t.closest(".mp-sched-cancel");
  if (cancel) {
    if (!window.confirm("이 예약/지연 이체를 취소하시겠습니까?")) return;
    const res = await apiFetch(`/api/transfers/${cancel.dataset.tid}/cancel`, { method: "POST" });
    if (res.ok) loadMyScheduled();
    return;
  }
});

/* ── Backoffice (관리자 전용) ───────────────────────────────────────── */
const BO_TABS = ["dashboard", "members", "transfers", "usage", "perf", "prompt", "products", "faq", "banners"];
const boLoaded = {};   // 탭별 최초 로드 여부(지연 로드)
let boUserPage = 1;
let boUserQuery = "";
let boUserRole = "";
let boTransferStatus = "";
let boTransferQuery = "";
const BO_PAGE_SIZE = 20;

// 보안 이벤트·접근 로그·이체 내역: "더 보기" 대신 30개/페이지 번호 페이지네이션(이미지 참고).
// 현재 페이지 번호만 들고 있다가 클릭 시 해당 페이지를 통째로 다시 그린다(append 아님).
let boTransferPage = 1;
let boSecurityPage = 1;
let boAccessLogPage = 1;
const BO_MONITOR_PAGE_SIZE = 30;
const boMonitorTotalPages = (total) => Math.max(1, Math.ceil(total / BO_MONITOR_PAGE_SIZE));

/* 1 ... 5 6 [7] 8 9 ... 155 형태의 페이지 번호 배열(현재 페이지 앞뒤 2개 + 처음/끝, 나머진 말줄임표) */
function paginationPages(current, total) {
  const pages = [1];
  const start = Math.max(2, current - 2);
  const end = Math.min(total - 1, current + 2);
  if (start > 2) pages.push("...");
  for (let p = start; p <= end; p++) pages.push(p);
  if (end < total - 1) pages.push("...");
  if (total > 1) pages.push(total);
  return pages;
}

/* 보안 이벤트/접근 로그/이체 내역 공용 페이지네이션 렌더러. list는 클릭 위임에서 어느
   loadBoXxx()를 부를지 구분하는 키("transfers"|"security"|"access-log"). */
function renderPagination(containerId, current, total, list) {
  const box = document.getElementById(containerId);
  if (!box) return;
  if (total <= 1) { box.innerHTML = ""; return; }
  const nums = paginationPages(current, total)
    .map((p) =>
      p === "..."
        ? `<span class="bo-page-dots">···</span>`
        : `<button type="button" class="bo-page-num${p === current ? " active" : ""}" data-list="${list}" data-page="${p}">${p}</button>`
    )
    .join("");
  box.innerHTML =
    `<button type="button" class="bo-page-nav" data-list="${list}" data-page="${current - 1}"${current <= 1 ? " disabled" : ""}>◀ 이전</button>` +
    nums +
    `<button type="button" class="bo-page-nav" data-list="${list}" data-page="${current + 1}"${current >= total ? " disabled" : ""}>다음 ▶</button>`;
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest(".bo-page-num, .bo-page-nav");
  if (!btn || btn.disabled) return;
  const page = Number(btn.dataset.page);
  if (!page || page < 1) return;
  const list = btn.dataset.list;
  if (list === "transfers") loadBoTransfers(page);
  else if (list === "security") loadBoSecurityEvents(page);
  else if (list === "access-log") loadBoAdminAccessLog(page);
  else if (list === "users") loadBoUsers(page);
  else if (list === "feedback") loadBoChatFeedback(page);
  else if (list === "inquiries") loadBoInquiries(page);
  else if (list === "my-inquiries") loadMyInquiries(page);
  else if (list === "my-statement") renderMyStatementList(page);
  else if (list === "acct-tx") showTransactions(acctTxAccountId, acctTxAccountNo, page);
  else if (list === "cc-history") loadBoChatbotHistory(page);
  else if (list === "documents") loadBoDocuments(page);
  else if (list === "special-products") loadBoSpecialProducts(page);
  else if (list === "notices") loadBoNotices(page);
  else if (list === "faqs") loadBoFaqs(page);
  else if (list === "events") loadBoEvents(page);
  else if (list === "banners") loadBoBanners(page);
  else if (list === "support-notices") loadNotices(page);
  else if (list === "support-faq") loadFaqs(page);
  else if (list === "support-documents") loadDocuments(page);
  else if (list === "support-events") loadEvents(page);
  else if (list === "support-inquiries") loadInquiries(page);
});

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
  // 메뉴 전환 시 이전 스크롤 위치가 그대로 남아 새 탭의 중간부터 보이던 문제 — navigate()의
  // 최상위 섹션 전환(main.js:175)과 동일하게 맨 위로 되돌린다.
  window.scrollTo(0, 0);
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

// 이체모니터링 라이브 갱신: 페이지네이션된 이체 내역 표 자체는 건드리지 않고(관리자가 몇 페이지를
//보고 있든 유지) 요약·예약 큐만 새로고침. 보안 이벤트·접근 로그는 1페이지를 보고 있을 때만 같이
// 새로고침 — 다른 페이지를 보고 있는데 10초마다 1페이지로 되돌아가면 혼란스러우므로 건너뛴다.
async function refreshBoTransfersLive() {
  loadBoScheduledQueue();
  if (boSecurityPage === 1) loadBoSecurityEvents(1);
  if (boAccessLogPage === 1) loadBoAdminAccessLog(1);
  try {
    const res = await apiFetch(`/api/admin/transfers?offset=0&limit=1&status=${boTransferStatus}`);
    if (res.ok) loadBoTransferSummaryWithTrend((await res.json()).summary);
  } catch (err) {
    console.error("이체 요약 갱신 실패:", err);
  }
}

function ensureBoTabLoaded(name) {
  if (boLoaded[name]) return;
  boLoaded[name] = true;
  if (name === "dashboard") loadBoDashboard();
  if (name === "members") loadBoUsers();
  if (name === "transfers") {
    loadBoTransfers(1);
    loadBoTransferPolicy();
    loadBoSecurityEvents(1);
    loadBoAdminAccessLog(1);
  }
  if (name === "usage") {
    loadBoUsageStats();
  }
  if (name === "perf") { loadBoPerfInfra(); loadBoInfraConfig(); loadBoBatchPerf(); loadBoFssStatus(); }
  if (name === "prompt") { loadBoChatbotConfig(); loadBoChatbotHistory(); loadBoChatFeedback(); }
  if (name === "products") {
    loadBoFssSummary();
    loadBoProductPreview("예금");
    loadTopProductsGrid("bo-product-stat-products");
    loadBoDocuments();
    loadBoSpecialProducts();
  }
  if (name === "faq") { loadBoNotices(); loadBoFaqs(); loadBoEvents(); loadBoInquiries(); }
  if (name === "banners") loadBoBanners();
}

document.addEventListener("click", (e) => {
  const tab = e.target.closest(".bo-tab");
  if (tab) boGoTab(tab.dataset.boTab);
  const kpi = e.target.closest(".metric.clickable[data-bo-goto]");
  if (kpi) boGoTab(kpi.dataset.boGoto);
});

/* 대시보드: 시스템 상태 스트립 + KPI 요약 + 이용 추이 + 은행별 비중/이체 상태 랭킹 */
async function loadBoDashboard() {
  try {
    const [usersRes, transfersRes, usageRes] = await Promise.all([
      apiFetch("/api/admin/users?limit=1"),
      apiFetch("/api/admin/transfers?limit=1"),
      apiFetch("/api/admin/usage-stats"),
    ]);
    const users = await usersRes.json();
    const transfers = await transfersRes.json();
    const usage = await usageRes.json();

    const transferTrend = await loadBoDashboardTransferTrend();
    renderBoDashboardSummary(users, transfers, usage, transferTrend);
    renderBoUsageDaily(usage.daily, "bo-dash-usage-daily");
    renderBoDashboardStatusRank(transfers.summary);
  } catch (err) {
    document.getElementById("bo-dash-summary").innerHTML = statError();
    console.error("대시보드 로드 실패:", err);
  }
  loadBoDashboardBankRank();
  loadBoDashboardInfra();
}

/* 대시보드 KPI 스파크라인용: 최근 이체 300건을 발생일별로 묶어 총 건수/완료금액
   추세만 뽑는다(이체모니터링 탭의 loadBoTransferSummaryWithTrend와 같은 방식,
   신규 백엔드 없이 기존 API 재사용). */
async function loadBoDashboardTransferTrend() {
  try {
    const res = await apiFetch(`/api/admin/transfers?offset=0&limit=300&status=`);
    if (!res.ok) return null;
    const { transfers } = await res.json();
    return bucketByDay(
      transfers,
      (t) => new Date(t.created_at * 1000).toISOString().slice(0, 10),
      (acc, t) => {
        acc = acc || { total: 0, completedAmount: 0 };
        acc.total++;
        if (t.status === "completed") acc.completedAmount += t.amount;
        return acc;
      }
    );
  } catch (err) {
    console.error("대시보드 이체 추세 로드 실패:", err);
    return null;
  }
}

const INFRA_STATUS_LABEL = { ok: "정상", warn: "주의", down: "연결안됨" };

/* /api/admin/infra-metrics 응답을 상태카드 4개(Kafka/ES/Phoenix/예약이체폴러) 배열로
   정규화 — 대시보드 스트립과 성능관리 "시스템 상태" 패널이 공유해서 쓴다. */
function buildInfraStatusCards(data) {
  return [
    { name: "Kafka", ...data.kafka },
    { name: "Elasticsearch", ...data.elasticsearch },
    { name: "Phoenix (LLM 추적)", ...data.phoenix },
    { name: "예약 이체 폴러", ...data.scheduled_poller },
  ];
}

function renderInfraStatusCards(targetId, cards) {
  const box = document.getElementById(targetId);
  if (!box) return;
  box.innerHTML = cards
    .map(
      (c) =>
        `<div class="status-card"><div class="status-card-head"><b>${escapeHtml(c.name)}</b>` +
        `<span class="status-badge ${c.status}">${INFRA_STATUS_LABEL[c.status] || c.status}</span></div>` +
        `<p>${escapeHtml(c.detail)}</p></div>`
    )
    .join("");
}

/* 대시보드: "지금 문제가 있는가" 최상단 배지 + 칩만 채운다(상세 상태카드·LLM·RAG
   지표는 성능관리 탭 소관 — loadBoPerfInfra() 참고). */
async function loadBoDashboardInfra() {
  const chipsBox = document.getElementById("bo-health-chips");
  try {
    const res = await apiFetch("/api/admin/infra-metrics");
    if (!res.ok) throw new Error("인프라 지표 조회 실패");
    const data = await res.json();
    const cards = buildInfraStatusCards(data);

    const okCount = cards.filter((c) => c.status === "ok").length;
    const cls = okCount === cards.length ? "ok" : okCount === 0 ? "down" : "warn";
    const label = cls === "ok" ? "정상 운영 중" : cls === "down" ? "장애 발생" : "일부 주의 필요";
    const badge = document.getElementById("bo-health-badge");
    if (badge) { badge.className = `status-badge lg ${cls}`; badge.textContent = `${okCount}/${cards.length} · ${label}`; }
    const updated = document.getElementById("bo-health-updated");
    if (updated) updated.textContent = "마지막 업데이트 방금 · 10초마다 자동 갱신";

    if (chipsBox) {
      chipsBox.innerHTML = cards
        .map((c) => `<span class="bo-health-chip ${c.status}"><span class="dot"></span>${escapeHtml(c.name)}</span>`)
        .join("");
    }
  } catch (err) {
    console.error("인프라 지표 로드 실패:", err);
  }
}

/* 성능관리: 인프라 연동 상태카드(4개) + LLM·RAG 24h 사용 지표 */
async function loadBoPerfInfra() {
  const cardsBox = document.getElementById("bo-health-cards");
  const metricBox = document.getElementById("bo-perf-llm-metrics");
  const cfgBox = document.getElementById("bo-perf-llm-config");
  if (cardsBox) cardsBox.textContent = "불러오는 중…";
  try {
    const res = await apiFetch("/api/admin/infra-metrics");
    if (!res.ok) throw new Error("인프라 지표 조회 실패");
    const data = await res.json();

    renderInfraStatusCards("bo-health-cards", buildInfraStatusCards(data));

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
    if (cardsBox) cardsBox.innerHTML = statError();
    console.error("인프라 지표 로드 실패:", err);
  }
}

/* KPI 5칸: 총 회원수/총 예치금(시계열 API가 없어 스파크라인 없는 "누적" 카드로 정직하게
   유지) + 총 이체 건수/이체 완료금액/오늘 이용 이벤트(스파크라인+증감 배지). 이체·회원
   카드는 클릭 시 해당 관리 탭으로 이동(boGoTab 재사용). */
function renderBoDashboardSummary(users, transfers, usage, transferTrend) {
  const daily = usage.daily || [];
  // daily는 이벤트가 있었던 날짜만 포함(공백 스킵)되므로, 엄밀한 캘린더상 "어제"가 아니라
  // "직전 데이터 존재일" 대비 증감이다. 데모 데이터셋 특성상 이 정도 근사로 충분하다.
  const today = daily.length ? daily[daily.length - 1].count : 0;
  const prevDay = daily.length > 1 ? daily[daily.length - 2].count : 0;
  const todayTrend = trendBadge(today, prevDay);
  const todayDayLabels = daily.map((d) => dayShort(d.day));
  const todayBars = daily.length >= 2
    ? sparkBars(daily.map((d) => d.count), "var(--blue)", todayDayLabels.map((d, i) => `${d} · ${daily[i].count}건`))
    : "";
  const todayCaption = daily.length >= 2
    ? `<div class="spark-caption"><span>${todayDayLabels[0]} → ${todayDayLabels[todayDayLabels.length - 1]}</span><span class="cap-val">${today}건</span></div>`
    : "";

  const tDays = transferTrend?.days || [];
  const tDayLabels = tDays.map(dayShort);
  const tSeries = tDays.map((d) => (transferTrend.byDay[d] && transferTrend.byDay[d].total) || 0);
  const aSeries = tDays.map((d) => (transferTrend.byDay[d] && transferTrend.byDay[d].completedAmount) || 0);
  const tBadge = tSeries.length >= 2 ? trendBadge(tSeries[tSeries.length - 1], tSeries[tSeries.length - 2]) : "";
  const aBadge = aSeries.length >= 2 ? trendBadge(aSeries[aSeries.length - 1], aSeries[aSeries.length - 2]) : "";
  const tBars = tSeries.length >= 2
    ? sparkBars(tSeries, "var(--blue)", tDayLabels.map((d, i) => `${d} · ${tSeries[i]}건`)) : "";
  const aBars = aSeries.length >= 2
    ? sparkBars(aSeries, "var(--blue)", tDayLabels.map((d, i) => `${d} · ${won(aSeries[i])}`)) : "";
  const tCaption = tSeries.length >= 2
    ? `<div class="spark-caption"><span>${tDayLabels[0]} → ${tDayLabels[tDayLabels.length - 1]}</span><span class="cap-val">${tSeries[tSeries.length - 1]}건</span></div>` : "";
  const aCaption = aSeries.length >= 2
    ? `<div class="spark-caption"><span>${tDayLabels[0]} → ${tDayLabels[tDayLabels.length - 1]}</span><span class="cap-val">${won(aSeries[aSeries.length - 1])}</span></div>` : "";

  document.getElementById("bo-dash-summary").innerHTML = `
    <div class="metric metric--label-top clickable" data-bo-goto="members"><div class="value">${users.total}</div><div class="label">총 회원수</div><div class="metric-note">누적</div></div>
    <div class="metric metric--label-top"><div class="value">${won(users.total_balance)}</div><div class="label">총 예치금</div><div class="metric-note">누적</div></div>
    <div class="metric metric--label-top clickable" data-bo-goto="transfers"><div class="value">${transfers.summary.total}${tBadge}</div><div class="label">총 이체 건수</div>${tBars}${tCaption}</div>
    <div class="metric metric--label-top clickable" data-bo-goto="transfers"><div class="value">${won(transfers.summary.completed_amount || 0)}${aBadge}</div><div class="label">이체 완료금액</div>${aBars}${aCaption}</div>
    <div class="metric metric--label-top"><div class="value">${today}${todayTrend}</div><div class="label">오늘 이용 이벤트</div>${todayBars}${todayCaption}</div>`;
}

/* 증감 퍼센트 배지. good: "up"(증가=좋음, 기본값) | "down"(감소=좋음) | "neutral"(볼륨성 지표라 항상 회색).
   화살표는 항상 실제 증감 방향을 가리키고, 배지 색만 good 기준으로 판단한다
   (예: 실패 건수가 늘면 good="down"이라 ▲ 이지만 빨간 down 스타일로 표시). */
function trendBadge(current, previous, good = "up") {
  if (previous === 0) {
    if (current === 0) return `<span class="trend-badge flat">− 0%</span>`;
    const cls = good === "down" ? "down" : good === "neutral" ? "flat" : "up";
    return `<span class="trend-badge ${cls}">▲ 신규</span>`;
  }
  const pct = Math.round(((current - previous) / previous) * 100);
  if (pct === 0) return `<span class="trend-badge flat">− 0%</span>`;
  const isUp = pct > 0;
  const cls = good === "neutral" ? "flat" : isUp === (good === "up") ? "up" : "down";
  return `<span class="trend-badge ${cls}">${isUp ? "▲" : "▼"} ${Math.abs(pct)}%</span>`;
}

/* 이체 상태 분포: 카테고리가 아니라 상태이므로 임의 색이 아니라 상태 토큰을 쓴다
   (완료=good, 대기=warning, 실패=error). */
function renderBoDashboardStatusRank(summary) {
  const data = [
    { label: "완료", value: summary.completed, color: "var(--blue-dark)" },
    { label: "대기", value: summary.pending, color: "var(--warning)" },
    { label: "실패", value: summary.failed, color: "var(--error)" },
    { label: "예약", value: summary.scheduled, color: "var(--info)" },
    { label: "지연", value: summary.delayed, color: "#6D28D9" },
    { label: "취소", value: summary.canceled, color: "var(--text-sub)" },
  ];
  renderRankedBars("bo-dash-rank-transfers", data, { totalId: "bo-dash-rank-transfers-total" });
}

async function loadBoDashboardBankRank() {
  try {
    const res = await fetch("/api/stats/banks?limit=20");
    const { banks } = await res.json();
    if (!banks.length) {
      document.getElementById("bo-dash-rank-banks").innerHTML = statEmpty();
      return;
    }
    const top = banks.slice(0, 5);
    const restTotal = banks.slice(5).reduce((s, b) => s + b.count, 0);
    const data = top.map((b) => ({ label: b.name, value: b.count }));
    if (restTotal > 0) data.push({ label: "기타", value: restTotal, color: "var(--text-sub)" });
    renderRankedBars("bo-dash-rank-banks", data, { totalId: "bo-dash-rank-banks-total" });
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
async function loadBoUsers(page = 1) {
  boUserPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(
      `/api/admin/users?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&q=${encodeURIComponent(boUserQuery)}&role=${encodeURIComponent(boUserRole)}`
    );
    if (!res.ok) throw new Error("회원 목록 조회 실패");
    const data = await res.json();
    renderBoUserSummary(data);
    renderBoUserRows(data.users);
    renderPagination("bo-user-pagination", page, boMonitorTotalPages(data.total), "users");
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

function renderBoUserRows(users) {
  const tbody = document.getElementById("bo-user-rows");
  if (!users.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="mp-empty">회원이 없습니다.</td></tr>`;
    return;
  }
  tbody.innerHTML = users
    .map((u) => {
      const d = new Date(u.created_at * 1000).toLocaleDateString("ko-KR");
      const statusBadge = u.is_active
        ? `<span class="status-badge ok">활성</span>`
        : `<span class="status-badge down">탈퇴</span>`;
      return `<tr><td>${escapeHtml(u.username)}</td><td>${escapeHtml(u.name)}</td>` +
        `<td>${escapeHtml(u.role)}</td><td>${statusBadge}</td><td>${d}</td>` +
        `<td><div class="bo-row-actions"><button class="btn btn-ghost" type="button" data-user-view="${u.id}">보기</button></div></td></tr>`;
    })
    .join("");
}

document.addEventListener("click", async (e) => {
  const viewBtn = e.target.closest("[data-user-view]");
  if (viewBtn) {
    showModal("<p>불러오는 중…</p>", true);
    try {
      const res = await apiFetch(`/api/admin/users/${viewBtn.dataset.userView}`);
      if (!res.ok) throw new Error("회원 상세 조회 실패");
      const data = await res.json();
      const u = data.user;
      const joined = new Date(u.created_at * 1000).toLocaleDateString("ko-KR");
      const accountRows = data.accounts.length
        ? data.accounts
            .map(
              (a) =>
                `<tr><td>${bankBadge(a.bank_name)} ${escapeHtml(a.bank_name)}</td>` +
                `<td>${escapeHtml(a.account_no)}</td><td>${won(a.balance)}</td>` +
                `<td>${a.is_primary ? '<span class="status-badge ok">주계좌</span>' : ""}</td></tr>`
            )
            .join("")
        : `<tr><td colspan="4" class="mp-empty">보유 계좌가 없습니다.</td></tr>`;
      showModal(
        `<h3>${escapeHtml(u.name)} (${escapeHtml(u.username)})</h3>` +
          `<div class="cf-row"><span>권한</span><b>${escapeHtml(u.role)}</b></div>` +
          `<div class="cf-row"><span>상태</span><b>${u.is_active ? "활성" : "탈퇴"}</b></div>` +
          `<div class="cf-row"><span>가입일</span><b>${joined}</b></div>` +
          `<h3 style="margin-top:20px">보유 계좌</h3>` +
          `<table class="admin-table"><thead><tr><th>은행</th><th>계좌번호</th><th>잔액</th><th></th></tr></thead>` +
          `<tbody>${accountRows}</tbody></table>`,
        true
      );
    } catch (err) {
      showModal(`<p>회원 상세를 불러오지 못했습니다.</p>`, true);
      console.error("회원 상세 로드 실패:", err);
    }
    return;
  }
  if (e.target.closest("#bo-user-search-btn")) {
    boUserQuery = document.getElementById("bo-user-search").value.trim();
    loadBoUsers(1);
  }
});

document.addEventListener("change", (e) => {
  if (e.target.id === "bo-user-role-filter") {
    boUserRole = e.target.value;
    loadBoUsers(1);
  }
});

/* 이체모니터링 */
async function loadBoTransfers(page = 1) {
  boTransferPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(
      `/api/admin/transfers?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&status=${boTransferStatus}&q=${encodeURIComponent(boTransferQuery)}`
    );
    if (!res.ok) throw new Error("이체 내역 조회 실패");
    const data = await res.json();
    renderBoTransferRows(data.transfers);
    renderPagination("bo-transfer-pagination", page, boMonitorTotalPages(data.total), "transfers");
    loadBoScheduledQueue();
    loadBoTransferSummaryWithTrend(data.summary);
  } catch (err) {
    console.error("이체 내역 로드 실패:", err);
  }
}

/* 지표 카드 스파크라인용: 전체 이체 내역(최대 300건)을 발생일별로 묶어 실제 추세를 계산한다.
   (표에 쓰는 페이지네이션된 목록과 별개 — 상태 필터와 무관하게 전체를 봐야 추세가 의미 있음) */
async function loadBoTransferSummaryWithTrend(summary) {
  let trend = null;
  try {
    const res = await apiFetch(`/api/admin/transfers?offset=0&limit=300&status=`);
    if (res.ok) {
      const { transfers } = await res.json();
      trend = bucketByDay(
        transfers,
        (t) => new Date(t.created_at * 1000).toISOString().slice(0, 10),
        (acc, t) => {
          acc = acc || { total: 0, completed: 0, pending: 0, failed: 0, scheduled: 0, delayed: 0, canceled: 0, completedAmount: 0 };
          acc.total++;
          acc[t.status] = (acc[t.status] || 0) + 1;
          if (t.status === "completed") acc.completedAmount += t.amount;
          return acc;
        }
      );
    }
  } catch (err) {
    console.error("이체 추세 로드 실패:", err);
  }
  renderBoTransferSummary(summary, trend);
}

/* 이체모니터링 8칸 지표 메타. valueColor 없으면 .value 기본색(브랜드 블루)을 그대로 쓴다.
   good: 그 지표가 늘어나는 게 관리자에게 좋은 신호인지("up"/"down") 아니면 단순 볼륨성이라
   판단할 게 아닌지("neutral") — trendBadge의 화살표 색(빨강/초록/회색)을 결정한다. */
const BO_TRANSFER_METRIC_META = {
  total:           { label: "총 이체",   sparkColor: "var(--text-sub)",  good: "neutral" },
  completed:       { label: "완료",      valueColor: "var(--blue-dark)", sparkColor: "var(--blue)",     good: "up" },
  pending:         { label: "대기",      valueColor: "var(--warning)",   sparkColor: "var(--warning)",  good: "down" },
  failed:          { label: "실패",      valueColor: "var(--error)",     sparkColor: "var(--error)",    good: "down" },
  scheduled:       { label: "예약",      valueColor: "var(--info)",      sparkColor: "var(--info)",     good: "neutral" },
  delayed:         { label: "지연",      valueColor: "#6D28D9",          sparkColor: "#6D28D9",         good: "down" },
  canceled:        { label: "취소",      valueColor: "var(--text-sub)",  sparkColor: "var(--text-sub)", good: "neutral" },
  completedAmount: { label: "완료 금액", sparkColor: "var(--blue)",      good: "up", amount: true },
};

/* 보안 이벤트 4칸 지표 메타 — 형식은 BO_TRANSFER_METRIC_META와 동일. */
const BO_SECURITY_METRIC_META = {
  total:         { label: "최근 24시간",   sparkColor: "var(--text-sub)", good: "down" },
  password_fail: { label: "비밀번호 실패", valueColor: "var(--error)",    sparkColor: "var(--error)",   good: "down" },
  limit:         { label: "한도 초과",     valueColor: "var(--warning)",  sparkColor: "var(--warning)", good: "down" },
  new_payee:     { label: "신규 수취계좌", valueColor: "var(--info)",     sparkColor: "var(--info)",    good: "neutral" },
};

/* meta 테이블 기반 공유 렌더러. values는 {지표키: 누적값}, trend는 bucketByDay() 결과.
   각 카드: 큰 값(+증감 배지) · 라벨 · 미니 막대 · "날짜범위 · 최근값" 캡션. */
function renderMetricGrid(containerId, values, trend, meta) {
  const box = document.getElementById(containerId);
  if (!box) return;
  const days = trend?.days || [];
  const dayLabels = days.map(dayShort);
  box.innerHTML = Object.keys(meta)
    .map((key) => {
      const m = meta[key];
      const value = values[key] || 0;
      const series = days.map((d) => (trend.byDay[d] && trend.byDay[d][key]) || 0);
      const fmtNum = (n) => (m.amount ? won(n) : `${n}건`);
      const valueText = m.amount ? won(value) : value;
      const valueStyle = m.valueColor ? ` style="color:${m.valueColor}"` : "";
      const badge = series.length >= 2
        ? trendBadge(series[series.length - 1], series[series.length - 2], m.good)
        : "";
      const titles = dayLabels.map((d, i) => `${d} · ${fmtNum(series[i])}`);
      const bars = sparkBars(series, m.sparkColor, titles);
      const caption = series.length >= 2
        ? `<div class="spark-caption"><span>${dayLabels[0]} → ${dayLabels[dayLabels.length - 1]}</span>` +
          `<span class="cap-val">${fmtNum(series[series.length - 1])}</span></div>`
        : "";
      return `<div class="metric metric--label-top">` +
        `<div class="value"${valueStyle}>${valueText}${badge}</div>` +
        `<div class="label">${escapeHtml(m.label)}</div>${bars}${caption}</div>`;
    })
    .join("");
}

function renderBoTransferSummary(s, trend) {
  const values = {
    total: s.total, completed: s.completed, pending: s.pending, failed: s.failed,
    scheduled: s.scheduled || 0, delayed: s.delayed || 0, canceled: s.canceled || 0,
    completedAmount: s.completed_amount || 0,
  };
  renderMetricGrid("bo-transfer-summary", values, trend, BO_TRANSFER_METRIC_META);
}

function renderBoSecuritySummary(summary, trend) {
  const bt = summary.by_type || {};
  const values = {
    total: summary.last_24h || 0,
    password_fail: bt.password_fail || 0,
    limit: (bt.limit_once || 0) + (bt.limit_daily || 0),
    new_payee: bt.new_payee || 0,
  };
  renderMetricGrid("bo-security-summary", values, trend, BO_SECURITY_METRIC_META);
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
        const mins = Math.ceil(remain / 60);
        let remainTxt;
        if (remain <= 0) remainTxt = "실행 임박";
        else if (mins < 60) remainTxt = `${mins}분 후`;
        else {
          const h = Math.floor(mins / 60), m = mins % 60;
          remainTxt = m ? `${h}시간 ${m}분 후` : `${h}시간 후`;
        }
        const label = BO_STATUS_LABEL[t.status] || t.status;
        return `<div class="sched-row">
          <span class="tx-status tx-${escapeHtml(t.status)}">${label}</span>
          <span class="sched-when">${when} · ${remainTxt}</span>
          <span class="sched-info">${escapeHtml(t.to_bank || "")} ${escapeHtml(t.to_account)}</span>
          <span class="sched-amount">${won(t.amount)}</span>
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

/* 지표 카드 스파크라인용: 최근 보안 이벤트(최대 300건)를 발생일별로 묶어 추세를 계산한다
   (표에 쓰는 페이지네이션된 목록과 별개 — loadBoTransferSummaryWithTrend와 동일한 이유). */
async function loadBoSecurityTrend() {
  try {
    const res = await apiFetch("/api/admin/security-events?offset=0&limit=300");
    if (!res.ok) return;
    const { events, summary } = await res.json();
    const trend = bucketByDay(
      events,
      (e) => new Date(e.created_at * 1000).toISOString().slice(0, 10),
      (acc, e) => {
        acc = acc || { total: 0, password_fail: 0, limit: 0, new_payee: 0 };
        acc.total++;
        if (e.event_type === "password_fail") acc.password_fail++;
        else if (e.event_type === "limit_once" || e.event_type === "limit_daily") acc.limit++;
        else if (e.event_type === "new_payee") acc.new_payee++;
        return acc;
      }
    );
    renderBoSecuritySummary(summary, trend);
  } catch (err) {
    console.error("보안 이벤트 추세 로드 실패:", err);
  }
}

/* 이체 보안 이벤트 목록: 고정 높이 스크롤 박스(.bo-scroll-list) 안에서 "더 보기"로 이어붙임
   (이체 내역 loadBoTransfers()와 동일한 offset 누적 append 패턴). */
async function loadBoSecurityEvents(page = 1) {
  const box = document.getElementById("bo-security-events");
  if (!box) return;
  boSecurityPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(
      `/api/admin/security-events?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}`
    );
    if (!res.ok) throw new Error("보안 이벤트 조회 실패");
    const { events, total } = await res.json();
    if (!events.length) {
      box.innerHTML = `<p class="tf-hint">기록된 보안 이벤트가 없습니다.</p>`;
    } else {
      box.innerHTML = events
        .map((e) => {
          const d = new Date(e.created_at * 1000).toLocaleString("ko-KR");
          const label = SEC_EVENT_LABEL[e.event_type] || escapeHtml(e.event_type);
          return `<div class="sec-row sec-row--${escapeHtml(e.event_type)}">
            <span class="sec-badge sec-${escapeHtml(e.event_type)}">${label}</span>
            <span class="sec-user">${escapeHtml(e.username || "-")}</span>
            <span class="sec-info">${escapeHtml(e.to_account || "")}</span>
            <span class="sec-amount">${won(e.amount)}</span>
            <span class="sec-when">${d}</span>
          </div>`;
        })
        .join("");
    }
    renderPagination("bo-security-pagination", page, boMonitorTotalPages(total), "security");
    if (page === 1) loadBoSecurityTrend();
  } catch (err) {
    box.innerHTML = statError();
    console.error("보안 이벤트 로드 실패:", err);
  }
}

/* 개인신용정보(계좌번호·금액 등) 접근 로그: 이체내역/회원상세 조회 시 backend/app.py의
   admin_transfers·admin_user_detail 핸들러가 db.log_admin_access()로 자동 기록한 것을 보여준다. */
const ADMIN_ACCESS_ACTION_LABEL = {
  view_transfers: "이체내역 조회",
  view_user_detail: "회원상세 조회",
};

const BO_ACCESS_LOG_METRIC_META = {
  total:      { label: "누적 조회",     sparkColor: "var(--text-sub)",  good: "neutral" },
  last24h:    { label: "최근 24시간",   valueColor: "var(--blue-dark)", sparkColor: "var(--blue)", good: "neutral" },
  transfers:  { label: "이체내역 조회", sparkColor: "var(--info)",      good: "neutral" },
  userDetail: { label: "회원상세 조회", sparkColor: "#6D28D9",          good: "neutral" },
};

function renderBoAdminAccessLogSummary(summary) {
  const ba = summary.by_action || {};
  const values = {
    total: summary.total || 0,
    last24h: summary.last_24h || 0,
    transfers: ba.view_transfers || 0,
    userDetail: ba.view_user_detail || 0,
  };
  // 트렌드(스파크라인)까지는 안 만듦 — 열람 로그는 추세보다 "지금 몇 건" 확인이 목적이라 간단한 지표만.
  renderMetricGrid("bo-access-log-summary", values, null, BO_ACCESS_LOG_METRIC_META);
}

async function loadBoAdminAccessLog(page = 1) {
  const tbody = document.getElementById("bo-access-log-rows");
  if (!tbody) return;
  boAccessLogPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(
      `/api/admin/access-log?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}`
    );
    if (!res.ok) throw new Error("접근 로그 조회 실패");
    const { logs, total, summary } = await res.json();
    if (!logs.length) {
      tbody.innerHTML = `<tr><td colspan="5">${statEmpty()}</td></tr>`;
    } else {
      tbody.innerHTML = logs
        .map((l) => {
          const d = new Date(l.created_at * 1000).toLocaleString("ko-KR");
          const label = ADMIN_ACCESS_ACTION_LABEL[l.action] || escapeHtml(l.action);
          return `<tr><td>${d}</td><td>${escapeHtml(l.admin_name || l.admin_username)}</td>` +
            `<td>${label}</td><td>${escapeHtml(l.target)}</td><td>${escapeHtml(l.detail)}</td></tr>`;
        })
        .join("");
    }
    renderPagination("bo-access-log-pagination", page, boMonitorTotalPages(total), "access-log");
    if (page === 1) renderBoAdminAccessLogSummary(summary);
  } catch (err) {
    console.error("접근 로그 로드 실패:", err);
  }
}

const BO_STATUS_LABEL = { completed: "완료", pending: "대기", failed: "실패",
  scheduled: "예약", delayed: "지연", canceled: "취소" };

function renderBoTransferRows(transfers) {
  const tbody = document.getElementById("bo-transfer-rows");
  if (!transfers.length) {
    tbody.innerHTML = `<tr><td colspan="7">${statEmpty()}</td></tr>`;
    return;
  }
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
      return `<tr class="bo-tx-row bo-tx-row--${escapeHtml(t.status)}"><td>${t.id}</td><td>${escapeHtml(t.from_account)}</td>` +
        `<td>${escapeHtml(t.to_account)}</td><td>${won(t.amount)}</td><td>${won(t.fee)}</td>` +
        `<td>${badge}${cancelBtn}</td><td>${d}</td></tr>`;
    })
    .join("");
  tbody.innerHTML = rows;
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
    loadBoTransfers(boTransferPage);   // 보던 페이지 그대로 새로고침
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
    loadBoTransfers(1);
  }
  if (e.target.closest("#bo-transfer-search-btn")) {
    boTransferQuery = document.getElementById("bo-transfer-search").value.trim();
    loadBoTransfers(1);
  }
});

/* 이용통계: 요약(전체/조회/검색) + 최근 14일 추이 */
async function loadBoUsageStats() {
  try {
    const res = await apiFetch("/api/admin/usage-stats");
    if (!res.ok) throw new Error("이용통계 조회 실패");
    const { summary, daily, categories } = await res.json();
    renderBoUsageSummary(summary);
    renderBoUsageDaily(daily);
    renderBoUsageSourceRank(summary);
    renderBoUsageCategoryRank(categories);
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

function renderBoUsageSourceRank(s) {
  renderRankedBars(
    "bo-usage-source-rank",
    [
      { label: "조회", value: s.view },
      { label: "검색", value: s.search },
    ],
    { totalId: "bo-usage-source-rank-total" }
  );
}

function renderBoUsageCategoryRank(categories) {
  const data = (categories || []).map((c) => ({ label: c.name, value: c.count }));
  renderRankedBars("bo-usage-category-rank", data, { totalId: "bo-usage-category-rank-total" });
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

/* 성능관리: 배치 테스트 성능 스냅샷 */
async function loadBoBatchPerf() {
  const box = document.getElementById("bo-batch-perf");
  try {
    const res = await fetch("data/stats.json");
    const { quality } = await res.json();
    if (!quality) { box.innerHTML = statEmpty(); return; }
    const hasAcc = quality.accuracy !== null && quality.accuracy !== undefined;
    const accCard = hasAcc
      ? `<div class="metric"><div class="value" style="color:#0B8457">${quality.accuracy}%</div><div class="label">정답률</div></div>`
      : `<div class="metric"><div class="value" style="color:#9AA0A6;font-size:16px">미측정</div><div class="label">정답률</div></div>`;
    const cats = (quality.categories || [])
      .map((c) => {
        const acc = (c.accuracy !== null && c.accuracy !== undefined)
          ? `<span class="p-acc">정답 ${c.accuracy}%</span>` : "";
        return `<li><span class="p-name">${escapeHtml(c.name)}</span>${acc}<span class="p-count">${c.count}문항</span></li>`;
      })
      .join("");
    box.innerHTML = `
      <div class="metric-grid">
        <div class="metric"><div class="value">${quality.total}</div><div class="label">총 테스트</div></div>
        <div class="metric"><div class="value">${quality.success_rate}%</div><div class="label">응답 성공률</div></div>
        ${accCard}
        <div class="metric"><div class="value">${quality.avg_latency_ms}ms</div><div class="label">평균 지연</div></div>
      </div>
      <p class="tf-hint">모델 <b>${escapeHtml(quality.model || "-")}</b> · ${escapeHtml(quality.provider || "-")} ·
        ${escapeHtml(quality.tested_at)}<br>
        응답 성공률 = 에러 없이 응답을 받은 비율(가용성) · 정답률 = LLM 채점 기준 정답 비율${hasAcc ? "" : " (배치 재실행 시 측정)"}</p>
      <ol class="topcat-list">${cats}</ol>`;
  } catch (err) {
    box.innerHTML = statError();
    console.error("배치 테스트 성능 로드 실패:", err);
  }
}

/* AI은행원 설정 (제공자/모델/답변스타일/시스템프롬프트/웹검색) — Backoffice 성능관리 */
let boChatbotProviders = {};

async function loadBoChatbotConfig() {
  const statusEl = document.getElementById("bo-cc-status");
  try {
    const res = await apiFetch("/api/admin/chatbot-config");
    if (!res.ok) throw new Error("은행원 설정을 불러오지 못했습니다.");
    const { config: cfg, providers } = await res.json();
    boChatbotProviders = providers;

    // 제공자는 현재 OpenAI 하나뿐이라(Gemini는 PROVIDERS에 미등록된 비활성 코드) 선택 UI
    // 대신 고정 텍스트로 표시하고, 실제 값은 히든 인풋에만 담아 저장/증감비교 로직을 그대로 재사용한다.
    const providerVal = cfg.provider || Object.keys(providers)[0] || "";
    document.getElementById("bo-cc-provider").value = providerVal;
    const providerDisplay = document.getElementById("bo-cc-provider-display");
    if (providerDisplay) providerDisplay.textContent = providerVal;
    fillBoChatbotModels(providerVal, cfg.default_model);

    document.getElementById("bo-cc-prompt").value = cfg.system_prompt || "";
    document.getElementById("bo-cc-websearch").checked = !!cfg.web_search;

    // 로드 직후 값을 스냅샷으로 저장 — 이후 변경 여부를 이 스냅샷과 비교해 "저장되지 않은
    // 변경사항" 배지를 띄운다(탭을 벗어나면 조용히 유실되던 문제 방지). boCcSavedPrompt는
    // A/B 비교의 "현재 저장된 프롬프트"(A) 쪽으로 그대로 재사용한다.
    boCcSnapshot = boCcCurrentValues();
    boCcSavedPrompt = cfg.system_prompt || "";
    updateBoCcCharCount();
    updateBoCcDirty();
  } catch (err) {
    if (statusEl) { statusEl.className = "tf-status err"; statusEl.textContent = err.message; }
    console.error("은행원 설정 로드 실패:", err);
  }
}

/* 모델 ID → 친숙한 이름 + 성능/비용 티어. 실제 저장값(ID)은 그대로 두고 표시만 바꾼다.
   "성능차이를 모르겠다"는 피드백 반영 — mini는 빠름·저렴, 나머지는 상대적 위치를 짧게 표기. */
const BO_MODEL_DISPLAY = {
  "gpt-4o-mini": { label: "GPT-4 mini", tier: "빠름·저렴" },
  "gpt-4o": { label: "GPT-4", tier: "표준" },
  "gpt-4.1-mini": { label: "GPT-4.1 mini", tier: "최신·경량" },
};
function boModelDisplay(id) {
  return BO_MODEL_DISPLAY[id] || { label: id, tier: "" };
}

function fillBoChatbotModels(provider, selected) {
  const modelSel = document.getElementById("bo-cc-model");
  const models = (boChatbotProviders[provider] && boChatbotProviders[provider].models) || [];
  modelSel.innerHTML = models
    .map((m) => {
      const d = boModelDisplay(m);
      const text = d.tier ? `${d.label} · ${d.tier}` : d.label;
      return `<option value="${escapeHtml(m)}" title="${escapeHtml(m)}">${escapeHtml(text)}</option>`;
    })
    .join("");
  if (models.includes(selected)) modelSel.value = selected;
}

/* 프롬프트 엔지니어링: 로드/저장 시점 스냅샷과 현재 폼 값을 비교해 미저장 변경사항 배지 표시 */
let boCcSnapshot = null;
let boCcSavedPrompt = "";   // 마지막으로 로드/저장된 시스템 프롬프트 — 프롬프트 A/B 비교의 "A" 값

function boCcCurrentValues() {
  return JSON.stringify({
    provider: document.getElementById("bo-cc-provider").value,
    model: document.getElementById("bo-cc-model").value,
    prompt: document.getElementById("bo-cc-prompt").value,
    websearch: document.getElementById("bo-cc-websearch").checked,
  });
}

function updateBoCcDirty() {
  const dirtyEl = document.getElementById("bo-cc-dirty");
  if (!dirtyEl || boCcSnapshot === null) return;
  dirtyEl.style.display = boCcCurrentValues() === boCcSnapshot ? "none" : "";
}

function updateBoCcCharCount() {
  const el = document.getElementById("bo-cc-charcount");
  const promptEl = document.getElementById("bo-cc-prompt");
  if (el && promptEl) el.textContent = `${promptEl.value.length}자`;
}

document.addEventListener("input", (e) => {
  if (!e.target.closest || !e.target.closest("#bo-chatbot-config-form")) return;
  if (e.target.id === "bo-cc-prompt") updateBoCcCharCount();
  updateBoCcDirty();
});
document.addEventListener("change", (e) => {
  if (!e.target.closest || !e.target.closest("#bo-chatbot-config-form")) return;
  updateBoCcDirty();
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
    boCcSnapshot = boCcCurrentValues();
    boCcSavedPrompt = document.getElementById("bo-cc-prompt").value;
    updateBoCcDirty();
    loadBoChatbotHistory(1);
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* ── Backoffice: 프롬프트 A/B 테스트 ──────────────────────────────── */
document.addEventListener("click", async (e) => {
  if (e.target.id !== "bo-ab-run") return;
  const btn = e.target;
  const statusEl = document.getElementById("bo-ab-status");
  const question = document.getElementById("bo-ab-question").value.trim();
  if (!question) {
    statusEl.className = "tf-status err";
    statusEl.textContent = "샘플 질문을 입력하세요.";
    return;
  }
  const respA = document.getElementById("bo-ab-resp-a");
  const respB = document.getElementById("bo-ab-resp-b");
  const latA = document.getElementById("bo-ab-lat-a");
  const latB = document.getElementById("bo-ab-lat-b");
  respA.textContent = respB.textContent = "";
  latA.textContent = latB.textContent = "";
  btn.disabled = true;
  statusEl.className = "tf-status";
  statusEl.textContent = "실행 중…";
  try {
    // 제공자·모델·답변 스타일은 위 AI은행원 설정의 현재 값을 공유하고, 프롬프트만 A(저장된 버전)/
    // B(지금 편집 중인 버전)로 다르게 — 저장 전에 변경이 실제로 응답을 어떻게 바꾸는지 미리 확인한다.
    const res = await apiFetch("/api/admin/prompt-ab-test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        provider: document.getElementById("bo-cc-provider").value,
        model: document.getElementById("bo-cc-model").value,
        prompt_a: boCcSavedPrompt,
        prompt_b: document.getElementById("bo-cc-prompt").value,
      }),
    });
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({}));
      throw new Error(detail || "비교 실행에 실패했습니다.");
    }
    const { a, b } = await res.json();
    respA.textContent = a.response;
    respB.textContent = b.response;
    latA.textContent = `${a.latency_ms.toLocaleString()} ms`;
    latB.textContent = `${b.latency_ms.toLocaleString()} ms`;
    statusEl.className = "tf-status ok";
    statusEl.textContent = "완료되었습니다.";
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

// A/B 비교 결과 선택: A(저장된 프롬프트)로 되돌리거나, B(지금 편집 중인 프롬프트)를 바로 저장.
document.addEventListener("click", (e) => {
  if (!e.target.closest("#bo-ab-pick-a")) return;
  const ta = document.getElementById("bo-cc-prompt");
  ta.value = boCcSavedPrompt;
  ta.dispatchEvent(new Event("input", { bubbles: true }));
  ta.scrollIntoView({ behavior: "smooth", block: "center" });
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#bo-ab-pick-b")) return;
  document.getElementById("bo-chatbot-config-form").requestSubmit();
});

/* ── Backoffice: 프롬프트 버전 이력(diff/되돌리기) ─────────────────── */

/* 두 텍스트를 줄 단위로 비교하는 순수 JS diff(LCS 기반, 외부 라이브러리 없음). */
function renderDiffLines(oldText, newText) {
  const a = (oldText || "").split("\n");
  const b = (newText || "").split("\n");
  const n = a.length, m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const result = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { result.push({ type: "same", line: a[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { result.push({ type: "del", line: a[i] }); i++; }
    else { result.push({ type: "add", line: b[j] }); j++; }
  }
  while (i < n) { result.push({ type: "del", line: a[i] }); i++; }
  while (j < m) { result.push({ type: "add", line: b[j] }); j++; }
  return result;
}

/* AI 답변 피드백(좋아요/싫어요) — 프롬프트 관리 탭 */
let boFeedbackCache = {};
let boFeedbackPage = 1;
let boFeedbackFilter = "down";
const BO_FEEDBACK_RATING_LABEL = { up: "좋아요", down: "싫어요" };

async function loadBoChatFeedback(page = 1) {
  boFeedbackPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(
      `/api/admin/chat-feedback?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&rating=${boFeedbackFilter}`
    );
    if (!res.ok) throw new Error("피드백 조회 실패");
    const data = await res.json();
    renderBoFeedbackSummary(data.summary);
    renderBoFeedbackReasonRank(data.reason_counts);
    renderBoFeedbackRows(data.items);
    renderPagination("bo-feedback-pagination", page, boMonitorTotalPages(data.total), "feedback");
  } catch (err) {
    console.error("피드백 로드 실패:", err);
  }
}

function renderBoFeedbackSummary(s) {
  const rate = s.total ? Math.round((s.down / s.total) * 100) : 0;
  document.getElementById("bo-feedback-summary").innerHTML = `
    <div class="metric"><div class="value">${s.total}</div><div class="label">총 평가</div></div>
    <div class="metric"><div class="value">${s.up}</div><div class="label">좋아요</div></div>
    <div class="metric"><div class="value">${s.down}</div><div class="label">싫어요</div></div>
    <div class="metric"><div class="value">${rate}%</div><div class="label">싫어요 비율</div></div>`;
  document.getElementById("bo-feedback-total").textContent = `총 ${s.total}건`;
}

function renderBoFeedbackReasonRank(reasonCounts) {
  const data = (reasonCounts || []).map((r) => ({ label: r.name, value: r.count }));
  renderRankedBars("bo-feedback-reason-rank", data);
}

function renderBoFeedbackRows(items) {
  boFeedbackCache = {};
  const tbody = document.getElementById("bo-feedback-rows");
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="mp-empty">피드백이 없습니다.</td></tr>`;
    return;
  }
  tbody.innerHTML = items
    .map((f) => {
      boFeedbackCache[f.id] = f;
      const d = new Date(f.created_at * 1000).toLocaleDateString("ko-KR");
      const badge = `<span class="status-badge ${f.rating === "up" ? "ok" : "down"}">${BO_FEEDBACK_RATING_LABEL[f.rating] || f.rating}</span>`;
      const reasons = (f.reasons || []).join(", ") || "-";
      const q = f.question ? escapeHtml(f.question.slice(0, 40)) : "-";
      return `<tr><td>${q}</td><td>${badge}</td><td>${escapeHtml(reasons)}</td><td>${d}</td>` +
        `<td><div class="bo-row-actions"><button class="btn btn-ghost" type="button" data-feedback-view="${f.id}">보기</button></div></td></tr>`;
    })
    .join("");
}

document.addEventListener("click", (e) => {
  const viewBtn = e.target.closest("[data-feedback-view]");
  if (viewBtn) {
    const f = boFeedbackCache[viewBtn.dataset.feedbackView];
    if (!f) return;
    const reasons = (f.reasons || []).join(", ") || "-";
    showModal(
      `<h3>${BO_FEEDBACK_RATING_LABEL[f.rating] || f.rating} 상세</h3>` +
        `<div class="cf-row"><span>등록일</span><b>${mpFmtDate(f.created_at)}</b></div>` +
        (f.rating === "down" ? `<div class="cf-row"><span>이유</span><b>${escapeHtml(reasons)}</b></div>` : "") +
        (f.comment ? `<div class="cf-row"><span>남긴 말</span><b>${escapeHtml(f.comment)}</b></div>` : "") +
        `<h3 style="margin-top:16px">질문</h3>` +
        `<div class="diff-box"><div class="diff-line same" style="white-space:pre-wrap; padding:12px;">${escapeHtml(f.question || "-")}</div></div>` +
        `<h3 style="margin-top:16px">답변</h3>` +
        `<div class="diff-box"><div class="diff-line same" style="white-space:pre-wrap; padding:12px;">${escapeHtml(f.answer || "-")}</div></div>`,
      true
    );
  }
});

document.addEventListener("change", (e) => {
  if (e.target.id === "bo-feedback-filter") {
    boFeedbackFilter = e.target.value;
    loadBoChatFeedback(1);
  }
});

let boCcHistoryCache = {};
let boCcHistoryPage = 1;
let boCcHistoryLatestId = null;

async function loadBoChatbotHistory(page = 1) {
  boCcHistoryPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(`/api/admin/chatbot-config/history?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}`);
    if (!res.ok) throw new Error("버전 이력 조회 실패");
    const data = await res.json();
    if (page === 1 && data.history.length) {
      boCcHistoryLatestId = data.history[0].id;
    }
    renderBoCcHistoryRows(data.history);
    renderPagination("bo-cc-history-pagination", page, boMonitorTotalPages(data.total), "cc-history");
  } catch (err) {
    console.error("버전 이력 로드 실패:", err);
  }
}

function renderBoCcHistoryRows(history) {
  boCcHistoryCache = {};
  const tbody = document.getElementById("bo-cc-history-rows");
  if (!history.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="mp-empty">저장된 이력이 없습니다.</td></tr>`;
    return;
  }
  tbody.innerHTML = history
    .map((h) => {
      boCcHistoryCache[h.id] = h;
      const when = mpFmtDate(h.created_at);
      const modelDisp = boModelDisplay(h.default_model);
      const summary = `<span class="model-badge" title="${escapeHtml(h.default_model)}">${escapeHtml(modelDisp.label)}</span>`;
      const actions = h.id === boCcHistoryLatestId
        ? `<div class="bo-row-actions"><span class="status-badge ok">현재 적용 중</span></div>`
        : `<div class="bo-row-actions">` +
          `<button class="btn btn-ghost" type="button" data-cc-diff="${h.id}">비교</button>` +
          `<button class="btn btn-ghost" type="button" data-cc-restore="${h.id}">되돌리기</button>` +
          `</div>`;
      return `<tr><td>${when}</td><td>${summary}</td><td>${escapeHtml(h.changed_by || "-")}</td><td>${actions}</td></tr>`;
    })
    .join("");
}

document.addEventListener("click", (e) => {
  const diffBtn = e.target.closest("[data-cc-diff]");
  if (!diffBtn) return;
  const entry = boCcHistoryCache[diffBtn.dataset.ccDiff];
  if (!entry) return;
  const currentPrompt = document.getElementById("bo-cc-prompt").value;
  const currentProvider = document.getElementById("bo-cc-provider").value;
  const currentModel = document.getElementById("bo-cc-model").value;
  const lines = renderDiffLines(entry.system_prompt, currentPrompt);
  const diffHtml = lines
    .map((l) => `<div class="diff-line ${l.type}">${escapeHtml(l.line) || "&nbsp;"}</div>`)
    .join("");
  const metaBits = [];
  if (entry.provider !== currentProvider) metaBits.push(`제공자: ${escapeHtml(entry.provider)} → ${escapeHtml(currentProvider)}`);
  if (entry.default_model !== currentModel) metaBits.push(`모델: ${escapeHtml(entry.default_model)} → ${escapeHtml(currentModel)}`);
  showModal(
    `<h3>${mpFmtDate(entry.created_at)} 버전 → 현재 비교</h3>` +
      (metaBits.length ? `<div class="diff-meta">${metaBits.join(" · ")}</div>` : "") +
      `<div class="diff-box">${diffHtml}</div>`,
    true
  );
});

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-cc-restore]");
  if (!btn) return;
  const entry = boCcHistoryCache[btn.dataset.ccRestore];
  if (!entry) return;
  if (!confirm(`${mpFmtDate(entry.created_at)} 저장된 버전으로 되돌릴까요? 되돌리기 전 상태도 이력에 남습니다.`)) return;
  try {
    const res = await apiFetch(`/api/admin/chatbot-config/history/${entry.id}/restore`, { method: "POST" });
    if (!res.ok) throw new Error("되돌리기에 실패했습니다.");
    await loadBoChatbotConfig();
    await loadBoChatbotHistory(1);
  } catch (err) {
    console.error("되돌리기 실패:", err);
    alert(err.message);
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
  const once = Number(document.getElementById("bo-tp-once").value);
  const daily = Number(document.getElementById("bo-tp-daily").value);
  const fee = Number(document.getElementById("bo-tp-fee").value);

  // 저장 전 관리자 확인 팝업
  const confirmed = window.confirm(
    "이체 정책을 이 값으로 저장할까요?\n\n" +
    `· 1회 한도: ${won(once)}\n` +
    `· 1일 한도: ${won(daily)}\n` +
    `· 타행 이체 수수료: ${won(fee)}\n\n` +
    "저장하면 이후 이체에 즉시 반영되고, AI은행원 유의사항 안내에도 새 한도가 반영됩니다."
  );
  if (!confirmed) {
    statusEl.className = "tf-status";
    statusEl.textContent = "저장을 취소했습니다.";
    return;
  }

  statusEl.className = "tf-status";
  statusEl.textContent = "저장 중…";
  try {
    const res = await apiFetch("/api/admin/transfer-policy", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        transfer_limit: once,
        daily_transfer_limit: daily,
        transfer_fee: fee,
      }),
    });
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({}));
      throw new Error(detail || "저장에 실패했습니다.");
    }
    statusEl.className = "tf-status ok";
    statusEl.textContent = "저장되었습니다. AI은행원 유의사항에도 반영됩니다.";
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* ── Backoffice: FSS 데이터 현황 ─────────────────────────────────── */
const FSS_STATUS_LABEL = { ok: "정상", down: "실패" };

async function loadBoFssStatus() {
  const cards = document.getElementById("bo-fss-cards");
  const changesBox = document.getElementById("bo-fss-changes");
  if (!cards) return;
  cards.innerHTML = `<p class="tf-hint">불러오는 중… (FSS 실시간 조회)</p>`;
  changesBox.innerHTML = "";
  try {
    const res = await apiFetch("/api/admin/fss-status");
    if (!res.ok) throw new Error("FSS 현황 조회 실패");
    const d = await res.json();

    const byCat = d.by_category || {};
    const catTxt = Object.keys(byCat).length
      ? Object.entries(byCat).map(([k, v]) => `${escapeHtml(k)} ${v}`).join(" · ") : "-";
    const ingestTxt = d.ingest && d.ingest.ingested_at
      ? escapeHtml(d.ingest.ingested_at.replace("T", " ")) : "미색인";
    cards.innerHTML = `
      <div class="metric"><div class="value"><span class="status-badge ${d.status}">${FSS_STATUS_LABEL[d.status] || d.status}</span></div><div class="label">FSS 키 상태</div></div>
      <div class="metric"><div class="value">${d.products || 0}</div><div class="label">총 상품 수</div></div>
      <div class="metric"><div class="value" style="font-size:18px">${escapeHtml(d.latest_dcls || "-")}</div><div class="label">최신 공시일</div></div>
      <div class="metric"><div class="value" style="font-size:15px">${ingestTxt}</div><div class="label">마지막 색인(RAG)</div></div>`;

    if (d.status !== "ok") {
      changesBox.innerHTML = `<p class="tf-hint" style="color:#C5221F">${escapeHtml(d.detail || "FSS 데이터를 불러오지 못했습니다.")}</p>`;
      return;
    }
    if (Object.keys(byCat).length) {
      changesBox.innerHTML = `<p class="tf-hint">카테고리: ${catTxt}</p>`;
    }
    const cs = d.changes_summary;
    if (!cs || !cs.has_snapshot) {
      changesBox.innerHTML += `<p class="tf-hint">기준 스냅샷이 없습니다. 아래 "현재 상태를 기준으로 저장"을 눌러 기준을 만들면, 이후 FSS 데이터 변경을 감지합니다.</p>`;
      return;
    }
    changesBox.innerHTML += `
      <div class="metric-grid" style="margin:12px 0">
        <div class="metric"><div class="value" style="color:#1A56DB">${cs.new}</div><div class="label">신규</div></div>
        <div class="metric"><div class="value" style="color:#92400E">${cs.rate_changed}</div><div class="label">금리 변경</div></div>
        <div class="metric"><div class="value" style="color:#5F6368">${cs.removed}</div><div class="label">삭제</div></div>
      </div>`;
    const ch = d.changes || {};
    const rows = [
      ...(ch.rate_changed || []).map((c) => {
        const [bank, name] = c.key.split("|");
        return `<div class="sec-row"><span class="sec-badge sec-limit_once">금리변경</span><span class="sec-user">${escapeHtml(bank)}</span><span class="sec-info">${escapeHtml(name)}</span><span class="sec-amount">${c.old ?? "-"}% → ${c.new ?? "-"}%</span></div>`;
      }),
      ...(ch.new || []).map((k) => {
        const [bank, name] = k.split("|");
        return `<div class="sec-row"><span class="sec-badge sec-new_payee">신규</span><span class="sec-user">${escapeHtml(bank)}</span><span class="sec-info">${escapeHtml(name)}</span><span class="sec-amount"></span></div>`;
      }),
    ].join("");
    if (rows) changesBox.innerHTML += `<div class="seclog">${rows}</div>`;
  } catch (err) {
    cards.innerHTML = statError();
    console.error("FSS 현황 로드 실패:", err);
  }
}

document.addEventListener("click", async (e) => {
  if (e.target.id !== "bo-fss-snapshot-btn") return;
  const statusEl = document.getElementById("bo-fss-snapshot-status");
  e.target.disabled = true;
  statusEl.className = "tf-status";
  statusEl.textContent = "기준 저장 중… (FSS 조회)";
  try {
    const res = await apiFetch("/api/admin/fss-status/snapshot", { method: "POST" });
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({}));
      throw new Error(detail || "저장 실패");
    }
    const { count } = await res.json();
    statusEl.className = "tf-status ok";
    statusEl.textContent = `기준 저장 완료 (${count}건)`;
    loadBoFssStatus();
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  } finally {
    e.target.disabled = false;
  }
});

/* ── Backoffice: FAQ·공지사항·문의내역 관리 — 칩으로 목록 전환(다 이미 로드돼 있고 표시만 토글) ── */
document.addEventListener("click", (e) => {
  const tab = e.target.closest(".bo-faq-tab");
  if (!tab) return;
  const name = tab.dataset.boFaqTab;
  document.querySelectorAll(".bo-faq-tab").forEach((b) => b.classList.toggle("active", b === tab));
  document.getElementById("bo-faqtab-notices").style.display = name === "notices" ? "" : "none";
  document.getElementById("bo-faqtab-faq").style.display = name === "faq" ? "" : "none";
  document.getElementById("bo-faqtab-events").style.display = name === "events" ? "" : "none";
  document.getElementById("bo-faqtab-inquiries").style.display = name === "inquiries" ? "" : "none";
});

/* ── Backoffice: 공지사항 관리 ───────────────────────────────────── */
let boNoticePage = 1, boNoticeQuery = "";
let boNoticeCache = {};
let boNoticeEditId = null;

async function loadBoNotices(page = 1) {
  boNoticePage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(
      `/api/notices?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&q=${encodeURIComponent(boNoticeQuery)}`
    );
    if (!res.ok) throw new Error("공지사항 목록 조회 실패");
    const data = await res.json();
    renderBoNoticeRows(data.notices);
    renderPagination("bo-notice-pagination", page, boMonitorTotalPages(data.total), "notices");
  } catch (err) {
    console.error("공지사항 목록 로드 실패:", err);
  }
}

function renderBoNoticeRows(notices) {
  boNoticeCache = {};
  const tbody = document.getElementById("bo-notice-rows");
  if (!notices.length) {
    tbody.innerHTML = `<tr><td colspan="3" class="mp-empty">등록된 공지사항이 없습니다.</td></tr>`;
    return;
  }
  tbody.innerHTML = notices
    .map((n) => {
      boNoticeCache[n.id] = n;
      const d = new Date(n.created_at * 1000).toLocaleDateString("ko-KR");
      return `<tr data-id="${n.id}"><td>${escapeHtml(n.title)}</td><td>${d}</td>` +
        `<td><div class="bo-row-actions">` +
        `<button class="btn btn-ghost bo-edit-btn" type="button" data-kind="notice" data-id="${n.id}">수정</button>` +
        `<button class="btn btn-ghost bo-del-btn" type="button" data-kind="notice" data-id="${n.id}">삭제</button>` +
        `</div></td></tr>`;
    })
    .join("");
}

function boNoticeStartEdit(id) {
  const n = boNoticeCache[id];
  if (!n) return;
  boNoticeEditId = id;
  document.getElementById("bo-notice-title").value = n.title;
  document.getElementById("bo-notice-content").value = n.content;
  document.getElementById("bo-notice-submit-btn").textContent = "수정 완료";
  document.getElementById("bo-notice-cancel-btn").style.display = "";
  document.getElementById("bo-notice-form").scrollIntoView({ behavior: "smooth", block: "start" });
  document.querySelectorAll("#bo-notice-rows tr.editing").forEach((tr) => tr.classList.remove("editing"));
  document.querySelector(`#bo-notice-rows tr[data-id="${id}"]`)?.classList.add("editing");
}

function boNoticeCancelEdit() {
  boNoticeEditId = null;
  document.getElementById("bo-notice-form").reset();
  document.getElementById("bo-notice-submit-btn").textContent = "등록";
  document.getElementById("bo-notice-cancel-btn").style.display = "none";
  document.querySelectorAll("#bo-notice-rows tr.editing").forEach((tr) => tr.classList.remove("editing"));
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#bo-notice-search-btn")) {
    boNoticeQuery = document.getElementById("bo-notice-search").value.trim();
    loadBoNotices(1);
  }
  const editBtn = e.target.closest('.bo-edit-btn[data-kind="notice"]');
  if (editBtn) boNoticeStartEdit(editBtn.dataset.id);
  if (e.target.closest("#bo-notice-cancel-btn")) boNoticeCancelEdit();
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "bo-notice-form") return;
  e.preventDefault();
  const statusEl = document.getElementById("bo-notice-form-status");
  statusEl.className = "tf-status";
  statusEl.textContent = boNoticeEditId ? "수정 중…" : "등록 중…";
  try {
    const payload = {
      title: document.getElementById("bo-notice-title").value.trim(),
      content: document.getElementById("bo-notice-content").value.trim(),
    };
    const res = boNoticeEditId
      ? await apiFetch(`/api/admin/notices/${boNoticeEditId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      : await apiFetch("/api/admin/notices", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
    if (!res.ok) throw new Error(boNoticeEditId ? "수정에 실패했습니다." : "등록에 실패했습니다.");
    statusEl.textContent = "";
    boNoticeCancelEdit();
    loadBoNotices(1);
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* ── Backoffice: FAQ 관리 ───────────────────────────────────────── */
let boFaqPage = 1, boFaqQuery = "";
let boFaqCache = {};
let boFaqEditId = null;

async function loadBoFaqs(page = 1) {
  boFaqPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(
      `/api/faqs?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&q=${encodeURIComponent(boFaqQuery)}`
    );
    if (!res.ok) throw new Error("FAQ 목록 조회 실패");
    const data = await res.json();
    renderBoFaqRows(data.faqs);
    renderPagination("bo-faq-pagination", page, boMonitorTotalPages(data.total), "faqs");
  } catch (err) {
    console.error("FAQ 목록 로드 실패:", err);
  }
}

function renderBoFaqRows(faqs) {
  boFaqCache = {};
  const tbody = document.getElementById("bo-faq-rows");
  if (!faqs.length) {
    tbody.innerHTML = `<tr><td colspan="3" class="mp-empty">등록된 FAQ가 없습니다.</td></tr>`;
    return;
  }
  tbody.innerHTML = faqs
    .map((f) => {
      boFaqCache[f.id] = f;
      const d = new Date(f.created_at * 1000).toLocaleDateString("ko-KR");
      return `<tr data-id="${f.id}"><td>${escapeHtml(f.question)}</td><td>${d}</td>` +
        `<td><div class="bo-row-actions">` +
        `<button class="btn btn-ghost bo-edit-btn" type="button" data-kind="faq" data-id="${f.id}">수정</button>` +
        `<button class="btn btn-ghost bo-del-btn" type="button" data-kind="faq" data-id="${f.id}">삭제</button>` +
        `</div></td></tr>`;
    })
    .join("");
}

function boFaqStartEdit(id) {
  const f = boFaqCache[id];
  if (!f) return;
  boFaqEditId = id;
  document.getElementById("bo-faq-question").value = f.question;
  document.getElementById("bo-faq-answer").value = f.answer;
  document.getElementById("bo-faq-submit-btn").textContent = "수정 완료";
  document.getElementById("bo-faq-cancel-btn").style.display = "";
  document.getElementById("bo-faq-form").scrollIntoView({ behavior: "smooth", block: "start" });
  document.querySelectorAll("#bo-faq-rows tr.editing").forEach((tr) => tr.classList.remove("editing"));
  document.querySelector(`#bo-faq-rows tr[data-id="${id}"]`)?.classList.add("editing");
}

function boFaqCancelEdit() {
  boFaqEditId = null;
  document.getElementById("bo-faq-form").reset();
  document.getElementById("bo-faq-submit-btn").textContent = "등록";
  document.getElementById("bo-faq-cancel-btn").style.display = "none";
  document.querySelectorAll("#bo-faq-rows tr.editing").forEach((tr) => tr.classList.remove("editing"));
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#bo-faq-search-btn")) {
    boFaqQuery = document.getElementById("bo-faq-search").value.trim();
    loadBoFaqs(1);
  }
  const editBtn = e.target.closest('.bo-edit-btn[data-kind="faq"]');
  if (editBtn) boFaqStartEdit(editBtn.dataset.id);
  if (e.target.closest("#bo-faq-cancel-btn")) boFaqCancelEdit();
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "bo-faq-form") return;
  e.preventDefault();
  const statusEl = document.getElementById("bo-faq-form-status");
  statusEl.className = "tf-status";
  statusEl.textContent = boFaqEditId ? "수정 중…" : "등록 중…";
  try {
    const payload = {
      question: document.getElementById("bo-faq-question").value.trim(),
      answer: document.getElementById("bo-faq-answer").value.trim(),
    };
    const res = boFaqEditId
      ? await apiFetch(`/api/admin/faqs/${boFaqEditId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      : await apiFetch("/api/admin/faqs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
    if (!res.ok) throw new Error(boFaqEditId ? "수정에 실패했습니다." : "등록에 실패했습니다.");
    statusEl.textContent = "";
    boFaqCancelEdit();
    loadBoFaqs(1);
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* ── Backoffice: 문의내역(읽기 전용 — 답변 기능 없음) ──────────────────── */
let boInquiryPage = 1, boInquiryQuery = "";
let boInquiryCache = {};

async function loadBoInquiries(page = 1) {
  boInquiryPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(
      `/api/admin/inquiries?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&q=${encodeURIComponent(boInquiryQuery)}`
    );
    if (!res.ok) throw new Error("문의내역 조회 실패");
    const data = await res.json();
    renderBoInquiryRows(data.inquiries);
    renderPagination("bo-inquiry-pagination", page, boMonitorTotalPages(data.total), "inquiries");
  } catch (err) {
    console.error("문의내역 로드 실패:", err);
  }
}

function renderBoInquiryRows(inquiries) {
  boInquiryCache = {};
  const tbody = document.getElementById("bo-inquiry-rows");
  if (!inquiries.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="mp-empty">문의 내역이 없습니다.</td></tr>`;
    return;
  }
  tbody.innerHTML = inquiries
    .map((iq) => {
      boInquiryCache[iq.id] = iq;
      const d = new Date(iq.created_at * 1000).toLocaleDateString("ko-KR");
      const author = escapeHtml(iq.name || iq.username);
      return `<tr><td>${author}</td><td>${escapeHtml(iq.title)}</td><td>${d}</td>` +
        `<td><div class="bo-row-actions">` +
        `<button class="btn btn-ghost" type="button" data-inquiry-view="${iq.id}">보기</button>` +
        `</div></td></tr>`;
    })
    .join("");
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#bo-inquiry-search-btn")) {
    boInquiryQuery = document.getElementById("bo-inquiry-search").value.trim();
    loadBoInquiries(1);
  }
  const viewBtn = e.target.closest("[data-inquiry-view]");
  if (viewBtn) {
    const iq = boInquiryCache[viewBtn.dataset.inquiryView];
    if (!iq) return;
    showModal(
      `<h3>${escapeHtml(iq.title)}</h3>` +
        `<div class="diff-meta">${escapeHtml(iq.name || iq.username)} · ${mpFmtDate(iq.created_at)}</div>` +
        `<div class="diff-box"><div class="diff-line same" style="white-space:pre-wrap; padding:12px;">${escapeHtml(iq.content)}</div></div>`,
      true
    );
  }
});

/* ── Backoffice: 상품관리 — 미리보기/통계/서식관리를 칩으로 전환(다 이미 로드돼 있고 표시만 토글) ── */
document.addEventListener("click", (e) => {
  const tab = e.target.closest(".bo-product-tab");
  if (!tab) return;
  const name = tab.dataset.boProductTab;
  document.querySelectorAll(".bo-product-tab").forEach((b) => b.classList.toggle("active", b === tab));
  document.getElementById("bo-producttab-preview").style.display = name === "preview" ? "" : "none";
  document.getElementById("bo-producttab-stats").style.display = name === "stats" ? "" : "none";
  document.getElementById("bo-producttab-documents").style.display = name === "documents" ? "" : "none";
  document.getElementById("bo-producttab-special").style.display = name === "special" ? "" : "none";
});

/* ── Backoffice: 실시간 상품 미리보기 (고객 페이지와 동일한 /api/products 재사용,
   단 렌더링은 확인용 테이블이라 아코디언/검색/정렬 없는 별도의 가벼운 렌더러) ── */
async function loadBoProductPreview(category) {
  const tbody = document.getElementById("bo-product-preview-rows");
  tbody.innerHTML = `<tr><td colspan="3">불러오는 중…</td></tr>`;
  try {
    const res = await apiFetch(`/api/products?category=${encodeURIComponent(category)}`);
    if (!res.ok) throw new Error("상품 조회 실패");
    const { products } = await res.json();
    renderBoProductPreviewRows(products);
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="3">${statError()}</td></tr>`;
    console.error("상품 미리보기 로드 실패:", err);
  }
}

function renderBoProductPreviewRows(products) {
  const tbody = document.getElementById("bo-product-preview-rows");
  if (!products.length) {
    tbody.innerHTML = `<tr><td colspan="3">${statEmpty()}</td></tr>`;
    return;
  }
  tbody.innerHTML = products
    .map((p) => {
      const { isLoan, minRate, maxRate } = getProductRateRange(p);
      let rateText = "-";
      if (minRate != null && maxRate != null) {
        rateText = minRate === maxRate
          ? `${maxRate.toFixed(2)}%`
          : isLoan
            ? `${minRate.toFixed(2)}~${maxRate.toFixed(2)}%`
            : `최고 ${maxRate.toFixed(2)}%`;
      }
      return `<tr><td>${bankBadge(p.bank)}</td><td>${escapeHtml(p.product_name)}</td><td>${rateText}</td></tr>`;
    })
    .join("");
}

document.addEventListener("click", (e) => {
  const tab = e.target.closest(".bo-cat-tab");
  if (!tab) return;
  document.querySelectorAll("#bo-product-cat-tabs .bo-cat-tab").forEach((t) => t.classList.toggle("active", t === tab));
  loadBoProductPreview(tab.dataset.category);
});

/* ── Backoffice: FSS 연동 상태 한 줄 요약 (성능관리 탭의 /api/admin/fss-status 재사용) ── */
async function loadBoFssSummary() {
  const badge = document.getElementById("bo-fss-key-badge");
  const text = document.getElementById("bo-fss-summary-text");
  try {
    const res = await apiFetch("/api/admin/fss-status");
    if (!res.ok) throw new Error("FSS 상태 조회 실패");
    const data = await res.json();
    const ok = data.status === "ok";
    badge.className = `status-badge ${ok ? "ok" : "warn"}`;
    badge.textContent = ok ? "FSS 연동 정상" : "FSS 연동 주의";
    text.textContent = `총 ${data.products ?? "-"}개 상품 · 최신 공시일 ${data.latest_dcls ?? "-"}`;
  } catch (err) {
    badge.className = "status-badge down";
    badge.textContent = "FSS 상태 확인 실패";
    text.textContent = "";
    console.error("FSS 요약 로드 실패:", err);
  }
}

document.addEventListener("click", (e) => {
  if (!e.target.closest("#bo-fss-detail-link")) return;
  boGoTab("perf");
});

/* ── Backoffice: 서식·약관·설명서 관리 ─────────────────────────────── */
let boDocPage = 1, boDocQuery = "";
let boDocCache = {};
let boDocEditId = null;

async function loadBoDocuments(page = 1) {
  boDocPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(
      `/api/documents?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&q=${encodeURIComponent(boDocQuery)}`
    );
    if (!res.ok) throw new Error("서식자료 목록 조회 실패");
    const data = await res.json();
    renderBoDocRows(data.documents);
    renderPagination("bo-doc-pagination", page, boMonitorTotalPages(data.total), "documents");
  } catch (err) {
    console.error("서식자료 목록 로드 실패:", err);
  }
}

function renderBoDocRows(docs) {
  boDocCache = {};
  const tbody = document.getElementById("bo-doc-rows");
  if (!docs.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="mp-empty">등록된 서식자료가 없습니다.</td></tr>`;
    return;
  }
  tbody.innerHTML = docs
    .map((doc) => {
      boDocCache[doc.id] = doc;
      const d = new Date(doc.created_at * 1000).toLocaleDateString("ko-KR");
      return `<tr data-id="${doc.id}"><td>${escapeHtml(doc.title)}</td><td>${escapeHtml(doc.category)}</td><td>${d}</td>` +
        `<td><div class="bo-row-actions">` +
        `<button class="btn btn-ghost bo-edit-btn" type="button" data-kind="document" data-id="${doc.id}">수정</button>` +
        `<button class="btn btn-ghost bo-del-btn" type="button" data-kind="document" data-id="${doc.id}">삭제</button>` +
        `</div></td></tr>`;
    })
    .join("");
}

function boDocStartEdit(id) {
  const doc = boDocCache[id];
  if (!doc) return;
  boDocEditId = id;
  document.getElementById("bo-doc-title").value = doc.title;
  document.getElementById("bo-doc-category").value = doc.category;
  document.getElementById("bo-doc-desc").value = doc.description || "";
  document.getElementById("bo-doc-submit-btn").textContent = "수정 완료";
  document.getElementById("bo-doc-cancel-btn").style.display = "";
  document.getElementById("bo-document-form").scrollIntoView({ behavior: "smooth", block: "start" });
  document.querySelectorAll("#bo-doc-rows tr.editing").forEach((tr) => tr.classList.remove("editing"));
  document.querySelector(`#bo-doc-rows tr[data-id="${id}"]`)?.classList.add("editing");
}

function boDocCancelEdit() {
  boDocEditId = null;
  document.getElementById("bo-document-form").reset();
  document.getElementById("bo-doc-submit-btn").textContent = "등록";
  document.getElementById("bo-doc-cancel-btn").style.display = "none";
  document.querySelectorAll("#bo-doc-rows tr.editing").forEach((tr) => tr.classList.remove("editing"));
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#bo-doc-search-btn")) {
    boDocQuery = document.getElementById("bo-doc-search").value.trim();
    loadBoDocuments(1);
  }
  const editBtn = e.target.closest('.bo-edit-btn[data-kind="document"]');
  if (editBtn) boDocStartEdit(editBtn.dataset.id);
  if (e.target.closest("#bo-doc-cancel-btn")) boDocCancelEdit();
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "bo-document-form") return;
  e.preventDefault();
  const statusEl = document.getElementById("bo-doc-form-status");
  statusEl.className = "tf-status";
  statusEl.textContent = boDocEditId ? "수정 중…" : "등록 중…";
  try {
    const payload = {
      title: document.getElementById("bo-doc-title").value.trim(),
      category: document.getElementById("bo-doc-category").value,
      description: document.getElementById("bo-doc-desc").value.trim(),
    };
    const res = boDocEditId
      ? await apiFetch(`/api/admin/documents/${boDocEditId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      : await apiFetch("/api/admin/documents", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
    if (!res.ok) throw new Error(boDocEditId ? "수정에 실패했습니다." : "등록에 실패했습니다.");
    statusEl.textContent = "";
    boDocCancelEdit();
    loadBoDocuments(1);
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* 공지사항/FAQ/서식자료 공용 삭제 버튼 */
const BO_DELETE_ENDPOINTS = {
  notice: { url: (id) => `/api/admin/notices/${id}`, reload: () => loadBoNotices(boNoticePage), label: "공지사항" },
  faq: { url: (id) => `/api/admin/faqs/${id}`, reload: () => loadBoFaqs(boFaqPage), label: "FAQ" },
  document: { url: (id) => `/api/admin/documents/${id}`, reload: () => loadBoDocuments(boDocPage), label: "서식자료" },
  event: { url: (id) => `/api/admin/events/${id}`, reload: () => loadBoEvents(boEventPage), label: "이벤트" },
  banner: { url: (id) => `/api/admin/banners/${id}`, reload: () => loadBoBanners(boBannerPage), label: "배너" },
  special_product: {
    url: (id) => `/api/admin/special-products/${id}`,
    reload: () => loadBoSpecialProducts(boSpecialPage),
    label: "특별상품",
  },
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

/* ── Backoffice: 특별상품 관리(FSS 비연동, 관리자 직접 등록) ──────────── */
let boSpecialPage = 1, boSpecialQuery = "";
let boSpecialCache = {};
let boSpecialEditId = null;

async function loadBoSpecialProducts(page = 1) {
  boSpecialPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(
      `/api/special-products?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&q=${encodeURIComponent(boSpecialQuery)}`
    );
    if (!res.ok) throw new Error("특별상품 목록 조회 실패");
    const data = await res.json();
    renderBoSpecialRows(data.special_products);
    renderPagination("bo-special-pagination", page, boMonitorTotalPages(data.total), "special-products");
  } catch (err) {
    console.error("특별상품 목록 로드 실패:", err);
  }
}

function renderBoSpecialRows(products) {
  boSpecialCache = {};
  const tbody = document.getElementById("bo-special-rows");
  if (!products.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="mp-empty">등록된 특별상품이 없습니다.</td></tr>`;
    return;
  }
  tbody.innerHTML = products
    .map((p) => {
      boSpecialCache[p.id] = p;
      return `<tr data-id="${p.id}"><td>${escapeHtml(p.title)}</td><td>${escapeHtml(p.bank_name)}</td><td>${escapeHtml(p.rate_text)}</td>` +
        `<td><div class="bo-row-actions">` +
        `<button class="btn btn-ghost bo-edit-btn" type="button" data-kind="special_product" data-id="${p.id}">수정</button>` +
        `<button class="btn btn-ghost bo-del-btn" type="button" data-kind="special_product" data-id="${p.id}">삭제</button>` +
        `</div></td></tr>`;
    })
    .join("");
}

function boSpecialStartEdit(id) {
  const p = boSpecialCache[id];
  if (!p) return;
  boSpecialEditId = id;
  document.getElementById("bo-special-title").value = p.title;
  document.getElementById("bo-special-bank").value = p.bank_name || "";
  document.getElementById("bo-special-rate").value = p.rate_text || "";
  document.getElementById("bo-special-badge").value = p.badge || "";
  document.getElementById("bo-special-desc").value = p.description || "";
  document.getElementById("bo-special-sort").value = p.sort_order || 0;
  document.getElementById("bo-special-submit-btn").textContent = "수정 완료";
  document.getElementById("bo-special-cancel-btn").style.display = "";
  document.getElementById("bo-special-form").scrollIntoView({ behavior: "smooth", block: "start" });
  document.querySelectorAll("#bo-special-rows tr.editing").forEach((tr) => tr.classList.remove("editing"));
  document.querySelector(`#bo-special-rows tr[data-id="${id}"]`)?.classList.add("editing");
}

function boSpecialCancelEdit() {
  boSpecialEditId = null;
  document.getElementById("bo-special-form").reset();
  document.getElementById("bo-special-submit-btn").textContent = "등록";
  document.getElementById("bo-special-cancel-btn").style.display = "none";
  document.querySelectorAll("#bo-special-rows tr.editing").forEach((tr) => tr.classList.remove("editing"));
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#bo-special-search-btn")) {
    boSpecialQuery = document.getElementById("bo-special-search").value.trim();
    loadBoSpecialProducts(1);
  }
  const editBtn = e.target.closest('.bo-edit-btn[data-kind="special_product"]');
  if (editBtn) boSpecialStartEdit(editBtn.dataset.id);
  if (e.target.closest("#bo-special-cancel-btn")) boSpecialCancelEdit();
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "bo-special-form") return;
  e.preventDefault();
  const statusEl = document.getElementById("bo-special-form-status");
  statusEl.className = "tf-status";
  statusEl.textContent = boSpecialEditId ? "수정 중…" : "등록 중…";
  try {
    const payload = {
      title: document.getElementById("bo-special-title").value.trim(),
      bank_name: document.getElementById("bo-special-bank").value.trim(),
      rate_text: document.getElementById("bo-special-rate").value.trim(),
      badge: document.getElementById("bo-special-badge").value.trim(),
      description: document.getElementById("bo-special-desc").value.trim(),
      sort_order: Number(document.getElementById("bo-special-sort").value) || 0,
    };
    const res = boSpecialEditId
      ? await apiFetch(`/api/admin/special-products/${boSpecialEditId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      : await apiFetch("/api/admin/special-products", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
    if (!res.ok) throw new Error(boSpecialEditId ? "수정에 실패했습니다." : "등록에 실패했습니다.");
    statusEl.textContent = "";
    boSpecialCancelEdit();
    loadBoSpecialProducts(1);
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* ── Backoffice: 이벤트 관리(응모자 보기 + 추첨 실행) ─────────────────── */
const dateInputToEpoch = (str) => new Date(str).getTime() / 1000;
const epochToDateInput = (epoch) => new Date(epoch * 1000).toISOString().slice(0, 10);

let boEventPage = 1, boEventQuery = "";
let boEventCache = {};
let boEventEditId = null;

async function loadBoEvents(page = 1) {
  boEventPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(
      `/api/events?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&q=${encodeURIComponent(boEventQuery)}`
    );
    if (!res.ok) throw new Error("이벤트 목록 조회 실패");
    const data = await res.json();
    renderBoEventRows(data.events);
    renderPagination("bo-event-pagination", page, boMonitorTotalPages(data.total), "events");
  } catch (err) {
    console.error("이벤트 목록 로드 실패:", err);
  }
}

function renderBoEventRows(events) {
  boEventCache = {};
  const tbody = document.getElementById("bo-event-rows");
  if (!events.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="mp-empty">등록된 이벤트가 없습니다.</td></tr>`;
    return;
  }
  tbody.innerHTML = events
    .map((ev) => {
      boEventCache[ev.id] = ev;
      const period = `${new Date(ev.start_at * 1000).toLocaleDateString("ko-KR")} ~ ${new Date(ev.end_at * 1000).toLocaleDateString("ko-KR")}`;
      const drawCell = !ev.is_drawing
        ? '<span class="tf-hint">-</span>'
        : ev.drawn_at
          ? '<span class="status-badge ok">추첨완료</span>'
          : `<button class="btn btn-ghost" type="button" data-event-draw="${ev.id}">추첨 실행</button>`;
      const entrantsBtn = ev.is_drawing
        ? `<button class="btn btn-ghost" type="button" data-event-entrants="${ev.id}">응모자 보기</button>`
        : "";
      return `<tr data-id="${ev.id}"><td>${escapeHtml(ev.title)}</td><td>${period}</td><td>${drawCell}</td>` +
        `<td><div class="bo-row-actions">` +
        entrantsBtn +
        `<button class="btn btn-ghost bo-edit-btn" type="button" data-kind="event" data-id="${ev.id}">수정</button>` +
        `<button class="btn btn-ghost bo-del-btn" type="button" data-kind="event" data-id="${ev.id}">삭제</button>` +
        `</div></td></tr>`;
    })
    .join("");
}

function boEventStartEdit(id) {
  const ev = boEventCache[id];
  if (!ev) return;
  boEventEditId = id;
  document.getElementById("bo-event-title").value = ev.title;
  document.getElementById("bo-event-content").value = ev.content || "";
  document.getElementById("bo-event-start").value = epochToDateInput(ev.start_at);
  document.getElementById("bo-event-end").value = epochToDateInput(ev.end_at);
  document.getElementById("bo-event-drawing").checked = !!ev.is_drawing;
  document.getElementById("bo-event-winner-count").value = ev.winner_count || 0;
  document.getElementById("bo-event-winner-count-field").style.display = ev.is_drawing ? "" : "none";
  document.getElementById("bo-event-submit-btn").textContent = "수정 완료";
  document.getElementById("bo-event-cancel-btn").style.display = "";
  document.getElementById("bo-event-form").scrollIntoView({ behavior: "smooth", block: "start" });
  document.querySelectorAll("#bo-event-rows tr.editing").forEach((tr) => tr.classList.remove("editing"));
  document.querySelector(`#bo-event-rows tr[data-id="${id}"]`)?.classList.add("editing");
}

function boEventCancelEdit() {
  boEventEditId = null;
  document.getElementById("bo-event-form").reset();
  document.getElementById("bo-event-winner-count-field").style.display = "none";
  document.getElementById("bo-event-submit-btn").textContent = "등록";
  document.getElementById("bo-event-cancel-btn").style.display = "none";
  document.querySelectorAll("#bo-event-rows tr.editing").forEach((tr) => tr.classList.remove("editing"));
}

document.addEventListener("change", (e) => {
  if (e.target.id === "bo-event-drawing") {
    document.getElementById("bo-event-winner-count-field").style.display = e.target.checked ? "" : "none";
  }
});

document.addEventListener("click", async (e) => {
  if (e.target.closest("#bo-event-search-btn")) {
    boEventQuery = document.getElementById("bo-event-search").value.trim();
    loadBoEvents(1);
  }
  const editBtn = e.target.closest('.bo-edit-btn[data-kind="event"]');
  if (editBtn) boEventStartEdit(editBtn.dataset.id);
  if (e.target.closest("#bo-event-cancel-btn")) boEventCancelEdit();

  const entrantsBtn = e.target.closest("[data-event-entrants]");
  if (entrantsBtn) {
    const eventId = entrantsBtn.dataset.eventEntrants;
    try {
      const res = await apiFetch(`/api/admin/events/${eventId}/entries?limit=100`);
      if (!res.ok) throw new Error("응모자 목록 조회 실패");
      const data = await res.json();
      const rows = data.entries
        .map(
          (en) =>
            `<tr><td>${escapeHtml(en.name || en.username)}</td><td>${mpFmtDate(en.created_at)}</td>` +
            `<td>${en.is_winner ? '<span class="status-badge ok">당첨</span>' : ""}</td></tr>`
        )
        .join("");
      showModal(
        `<h3>응모자 목록 (총 ${data.total}명)</h3>` +
          `<table class="admin-table"><thead><tr><th>이름</th><th>응모일</th><th></th></tr></thead>` +
          `<tbody>${rows || '<tr><td colspan="3" class="tf-hint">응모자가 없습니다.</td></tr>'}</tbody></table>`,
        true
      );
    } catch (err) {
      alert(err.message);
    }
  }

  const drawBtn = e.target.closest("[data-event-draw]");
  if (drawBtn) {
    if (!confirm("추첨을 실행하시겠습니까? 이미 실행된 추첨은 다시 실행되지 않습니다.")) return;
    try {
      const res = await apiFetch(`/api/admin/events/${drawBtn.dataset.eventDraw}/draw`, { method: "POST" });
      if (!res.ok) throw new Error("추첨 실행에 실패했습니다.");
      const data = await res.json();
      alert(`추첨 완료 — 당첨자 ${data.winners.length}명`);
      loadBoEvents(boEventPage);
    } catch (err) {
      alert(err.message);
    }
  }
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "bo-event-form") return;
  e.preventDefault();
  const statusEl = document.getElementById("bo-event-form-status");
  statusEl.className = "tf-status";
  statusEl.textContent = boEventEditId ? "수정 중…" : "등록 중…";
  try {
    const payload = {
      title: document.getElementById("bo-event-title").value.trim(),
      content: document.getElementById("bo-event-content").value.trim(),
      start_at: dateInputToEpoch(document.getElementById("bo-event-start").value),
      end_at: dateInputToEpoch(document.getElementById("bo-event-end").value),
      is_drawing: document.getElementById("bo-event-drawing").checked,
      winner_count: Number(document.getElementById("bo-event-winner-count").value) || 0,
    };
    const res = boEventEditId
      ? await apiFetch(`/api/admin/events/${boEventEditId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      : await apiFetch("/api/admin/events", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
    if (!res.ok) throw new Error(boEventEditId ? "수정에 실패했습니다." : "등록에 실패했습니다.");
    statusEl.textContent = "";
    boEventCancelEdit();
    loadBoEvents(1);
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* ── Backoffice: 배너 관리(공지·이벤트·특별상품 연결) ─────────────────── */
const BO_BANNER_LINK_LABEL = { none: "없음", notice: "공지사항", event: "이벤트", special_product: "특별상품" };
const BO_BANNER_LINK_ENDPOINTS = {
  notice: { url: "/api/notices?limit=50", items: (d) => d.notices, label: (n) => n.title },
  event: { url: "/api/events?limit=50", items: (d) => d.events, label: (n) => n.title },
  special_product: { url: "/api/special-products?limit=50", items: (d) => d.special_products, label: (n) => n.title },
};

let boBannerPage = 1, boBannerQuery = "";
let boBannerCache = {};
let boBannerEditId = null;

async function loadBoBanners(page = 1) {
  boBannerPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(
      `/api/admin/banners?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&q=${encodeURIComponent(boBannerQuery)}`
    );
    if (!res.ok) throw new Error("배너 목록 조회 실패");
    const data = await res.json();
    renderBoBannerRows(data.banners);
    renderPagination("bo-banner-pagination", page, boMonitorTotalPages(data.total), "banners");
  } catch (err) {
    console.error("배너 목록 로드 실패:", err);
  }
}

function renderBoBannerRows(banners) {
  boBannerCache = {};
  const tbody = document.getElementById("bo-banner-rows");
  if (!banners.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="mp-empty">등록된 배너가 없습니다.</td></tr>`;
    return;
  }
  tbody.innerHTML = banners
    .map((b) => {
      boBannerCache[b.id] = b;
      const activeBadge = b.is_active
        ? '<span class="status-badge ok">활성</span>'
        : '<span class="status-badge down">비활성</span>';
      const thumb = b.image_path
        ? `<img src="${b.image_path}" alt="" style="width:64px; height:auto; border-radius:4px;" />`
        : "";
      return `<tr data-id="${b.id}"><td>${thumb}</td><td>${escapeHtml(b.title)}</td>` +
        `<td>${BO_BANNER_LINK_LABEL[b.link_type] || b.link_type}</td><td>${b.sort_order}</td><td>${activeBadge}</td>` +
        `<td><div class="bo-row-actions">` +
        `<button class="btn btn-ghost bo-edit-btn" type="button" data-kind="banner" data-id="${b.id}">수정</button>` +
        `<button class="btn btn-ghost bo-del-btn" type="button" data-kind="banner" data-id="${b.id}">삭제</button>` +
        `</div></td></tr>`;
    })
    .join("");
}

async function boBannerRefreshLinkOptions(linkType, selectedId) {
  const field = document.getElementById("bo-banner-link-id-field");
  const select = document.getElementById("bo-banner-link-id");
  const endpoint = BO_BANNER_LINK_ENDPOINTS[linkType];
  if (!endpoint) {
    field.style.display = "none";
    select.innerHTML = "";
    return;
  }
  field.style.display = "";
  select.innerHTML = '<option value="">불러오는 중…</option>';
  try {
    const res = await fetch(endpoint.url);
    const data = await res.json();
    const items = endpoint.items(data) || [];
    select.innerHTML = items
      .map((item) => `<option value="${item.id}">${escapeHtml(endpoint.label(item))}</option>`)
      .join("") || '<option value="">등록된 항목이 없습니다</option>';
    if (selectedId) select.value = String(selectedId);
  } catch (err) {
    select.innerHTML = '<option value="">목록 조회 실패</option>';
  }
}

document.addEventListener("change", (e) => {
  if (e.target.id === "bo-banner-link-type") {
    boBannerRefreshLinkOptions(e.target.value);
  }
});

function boBannerStartEdit(id) {
  const b = boBannerCache[id];
  if (!b) return;
  boBannerEditId = id;
  document.getElementById("bo-banner-title").value = b.title;
  document.getElementById("bo-banner-subtitle").value = b.subtitle || "";
  document.getElementById("bo-banner-image").value = "";
  document.getElementById("bo-banner-image-name").textContent = b.image_path ? "기존 이미지 유지" : "선택된 파일 없음";
  const preview = document.getElementById("bo-banner-image-preview");
  if (b.image_path) {
    preview.src = b.image_path;
    preview.style.display = "";
  } else {
    preview.style.display = "none";
  }
  document.getElementById("bo-banner-sort").value = Math.min(5, Math.max(1, b.sort_order || 1));
  document.getElementById("bo-banner-active").checked = !!b.is_active;
  document.getElementById("bo-banner-link-type").value = b.link_type;
  boBannerRefreshLinkOptions(b.link_type, b.link_id);
  document.getElementById("bo-banner-submit-btn").textContent = "수정 완료";
  document.getElementById("bo-banner-cancel-btn").style.display = "";
  document.getElementById("bo-banner-form").scrollIntoView({ behavior: "smooth", block: "start" });
  document.querySelectorAll("#bo-banner-rows tr.editing").forEach((tr) => tr.classList.remove("editing"));
  document.querySelector(`#bo-banner-rows tr[data-id="${id}"]`)?.classList.add("editing");
}

function boBannerCancelEdit() {
  boBannerEditId = null;
  document.getElementById("bo-banner-form").reset();
  document.getElementById("bo-banner-link-id-field").style.display = "none";
  document.getElementById("bo-banner-image-preview").style.display = "none";
  document.getElementById("bo-banner-image-name").textContent = "선택된 파일 없음";
  document.getElementById("bo-banner-submit-btn").textContent = "등록";
  document.getElementById("bo-banner-cancel-btn").style.display = "none";
  document.querySelectorAll("#bo-banner-rows tr.editing").forEach((tr) => tr.classList.remove("editing"));
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#bo-banner-cancel-btn")) boBannerCancelEdit();
  const editBtn = e.target.closest('.bo-edit-btn[data-kind="banner"]');
  if (editBtn) boBannerStartEdit(editBtn.dataset.id);
});

document.getElementById("bo-banner-image").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  document.getElementById("bo-banner-image-name").textContent = file.name;
  const preview = document.getElementById("bo-banner-image-preview");
  preview.src = URL.createObjectURL(file);
  preview.style.display = "";
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "bo-banner-form") return;
  e.preventDefault();
  const statusEl = document.getElementById("bo-banner-form-status");
  statusEl.className = "tf-status";
  statusEl.textContent = boBannerEditId ? "수정 중…" : "등록 중…";
  try {
    const linkIdRaw = document.getElementById("bo-banner-link-id").value;
    const imageFile = document.getElementById("bo-banner-image").files[0];
    if (!boBannerEditId && !imageFile) throw new Error("배너 이미지를 선택해주세요.");
    const fd = new FormData();
    fd.append("title", document.getElementById("bo-banner-title").value.trim());
    fd.append("subtitle", document.getElementById("bo-banner-subtitle").value.trim());
    fd.append("link_type", document.getElementById("bo-banner-link-type").value);
    if (linkIdRaw) fd.append("link_id", linkIdRaw);
    fd.append("sort_order", Number(document.getElementById("bo-banner-sort").value) || 0);
    fd.append("is_active", document.getElementById("bo-banner-active").checked);
    if (imageFile) fd.append("image", imageFile);
    const res = boBannerEditId
      ? await apiFetch(`/api/admin/banners/${boBannerEditId}`, { method: "PUT", body: fd })
      : await apiFetch("/api/admin/banners", { method: "POST", body: fd });
    if (!res.ok) throw new Error(boBannerEditId ? "수정에 실패했습니다." : "등록에 실패했습니다.");
    statusEl.textContent = "";
    boBannerCancelEdit();
    loadBoBanners(1);
  } catch (err) {
    statusEl.className = "tf-status err";
    statusEl.textContent = err.message;
  }
});

/* ── Backoffice: 인프라 설정 (읽기전용, 성능관리 탭) ───── */
function boInfraRowValue(value) {
  if (value && typeof value === "object" && value.badge) {
    return `<span class="status-badge ${value.badge}">${escapeHtml(value.text)}</span>`;
  }
  return escapeHtml(String(value));
}

async function loadBoInfraConfig() {
  const box = document.getElementById("bo-infra-config");
  try {
    const res = await apiFetch("/api/admin/infra-config");
    if (!res.ok) throw new Error("인프라 설정 조회 실패");
    const cfg = await res.json();
    const rows = [
      ["OpenAI API 키", cfg.openai_key_set ? { badge: "ok", text: "설정됨" } : { badge: "down", text: "미설정" }],
      ["FSS API 키", cfg.fss_key_set ? { badge: "ok", text: "설정됨" } : { badge: "down", text: "미설정" }],
      ["AI은행원 제공자", cfg.chatbot_provider],
      ["AI은행원 모델", cfg.chatbot_model ? boModelDisplay(cfg.chatbot_model).label : "-"],
      ["웹 검색 사용", cfg.chatbot_web_search ? "사용" : "미사용"],
      ["시맨틱 캐시", cfg.cache_enabled ? "사용" : "미사용"],
      ["RAG 검색 결과 수", cfg.rag_top_k],
      ["캐시 유사도 임계값", cfg.cache_threshold],
      ["Redis TTL (초)", cfg.redis_ttl],
      ["Elasticsearch 주소", cfg.es_host],
      ["Redis 주소", `${cfg.redis_host}:${cfg.redis_port}`],
    ];
    box.innerHTML = `<ol class="topcat-list">${rows
      .map(([label, value]) => `<li><span class="p-name">${escapeHtml(label)}</span><span class="p-count">${boInfraRowValue(value)}</span></li>`)
      .join("")}</ol>`;
  } catch (err) {
    box.innerHTML = statError();
    console.error("인프라 설정 로드 실패:", err);
  }
}

/* ── 고객센터: 공지사항 / FAQ / 문의하기 / 서식·약관·설명서 ─────────── */
const SUPPORT_TABS = ["notices", "faq", "inquiry", "documents", "events"];
const supportLoaded = {};
let noticePage = 1, noticeQuery = "";
let faqPage = 1, faqQuery = "";
let documentPage = 1, documentQuery = "";

function supportGoTab(name) {
  if (!SUPPORT_TABS.includes(name)) name = "notices";
  document.querySelectorAll(".support-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.supportTab === name)
  );
  SUPPORT_TABS.forEach((t) => {
    document.getElementById(`support-panel-${t}`).style.display = t === name ? "block" : "none";
  });
  document.querySelectorAll(".support-search").forEach((box) => {
    box.style.display = box.dataset.supportSearch === name ? "flex" : "none";
  });
  ensureSupportTabLoaded(name);
}

function ensureSupportTabLoaded(name) {
  if (name === "inquiry") { updateInquiryView(); return; }   // 로그인 상태가 바뀔 수 있어 매번 갱신
  if (name === "events") { loadEvents(eventPage); return; }  // 응모 상태가 로그인에 따라 달라져 매번 갱신(보던 페이지 유지)
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
async function loadNotices(page = 1) {
  noticePage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await fetch(`/api/notices?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&q=${encodeURIComponent(noticeQuery)}`);
    const data = await res.json();
    renderNoticeList(data.notices);
    renderPagination("notice-pagination", page, boMonitorTotalPages(data.total), "support-notices");
  } catch (err) {
    console.error("공지사항 로드 실패:", err);
  }
}

function renderNoticeList(notices) {
  const box = document.getElementById("notice-list");
  if (!notices.length) { box.innerHTML = '<p class="support-empty">등록된 공지사항이 없습니다.</p>'; return; }
  box.innerHTML = notices
    .map(
      (n) =>
        `<div class="faq-item"><div class="faq-q">${escapeHtml(n.title)} ${CHEV_SVG}</div>` +
        `<div class="faq-a"><div class="tf-hint">${fmtDate(n.created_at)}</div>${escapeHtml(n.content)}</div></div>`
    )
    .join("");
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#notice-search-btn")) {
    noticeQuery = document.getElementById("notice-search").value.trim();
    loadNotices(1);
  }
});

/* FAQ */
async function loadFaqs(page = 1) {
  faqPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await fetch(`/api/faqs?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&q=${encodeURIComponent(faqQuery)}`);
    const data = await res.json();
    renderFaqList(data.faqs);
    renderPagination("faq-pagination", page, boMonitorTotalPages(data.total), "support-faq");
  } catch (err) {
    console.error("FAQ 로드 실패:", err);
  }
}

function renderFaqList(faqs) {
  const box = document.getElementById("faq-list");
  if (!faqs.length) { box.innerHTML = '<p class="support-empty">등록된 FAQ가 없습니다.</p>'; return; }
  box.innerHTML = faqs
    .map(
      (f) =>
        `<div class="faq-item"><div class="faq-q">${escapeHtml(f.question)} ${CHEV_SVG}</div>` +
        `<div class="faq-a">${escapeHtml(f.answer)}</div></div>`
    )
    .join("");
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#faq-search-btn")) {
    faqQuery = document.getElementById("faq-search").value.trim();
    loadFaqs(1);
  }
});

/* 서식·약관·설명서 */
async function loadDocuments(page = 1) {
  documentPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await fetch(`/api/documents?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&q=${encodeURIComponent(documentQuery)}`);
    const data = await res.json();
    renderDocumentList(data.documents);
    renderPagination("document-pagination", page, boMonitorTotalPages(data.total), "support-documents");
  } catch (err) {
    console.error("서식·약관·설명서 로드 실패:", err);
  }
}

function renderDocumentList(documents) {
  const box = document.getElementById("document-list");
  if (!documents.length) { box.innerHTML = '<p class="support-empty">등록된 서식·약관·설명서가 없습니다.</p>'; return; }
  box.innerHTML = documents
    .map(
      (d) =>
        `<div class="document-item"><div class="doc-title">${escapeHtml(d.title)} ` +
        `<span class="doc-badge">${escapeHtml(d.category)}</span></div>` +
        `<p>${escapeHtml(d.description)}</p></div>`
    )
    .join("");
}

document.addEventListener("click", (e) => {
  if (e.target.closest("#document-search-btn")) {
    documentQuery = document.getElementById("document-search").value.trim();
    loadDocuments(1);
  }
});

/* 이벤트 (추첨형은 로그인 시 응모 가능) */
let eventPage = 1, eventQuery = "";

async function loadEvents(page = 1) {
  eventPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await fetch(`/api/events?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}&q=${encodeURIComponent(eventQuery)}`);
    const data = await res.json();
    renderEventList(data.events);
    renderPagination("event-pagination", page, boMonitorTotalPages(data.total), "support-events");
    updateEventEntryStates(data.events);
  } catch (err) {
    console.error("이벤트 로드 실패:", err);
  }
}

function renderEventList(events) {
  const box = document.getElementById("event-list");
  if (!events.length) { box.innerHTML = '<p class="support-empty">등록된 이벤트가 없습니다.</p>'; return; }
  box.innerHTML = events
    .map((ev) => {
      const period = `${fmtDate(ev.start_at)} ~ ${fmtDate(ev.end_at)}`;
      const drawBadge = ev.is_drawing ? '<span class="status-badge ok">추첨 이벤트</span> ' : "";
      let actionHtml = "";
      if (ev.is_drawing) {
        if (ev.drawn_at) {
          // 당첨자 발표는 이 글에 덧붙이지 않고 "[당첨자 발표] ..." 별도 게시글로 분리되어 있다(추첨 실행 시 자동 생성).
          actionHtml = '<p class="tf-hint">추첨이 종료되었습니다. 목록에서 "[당첨자 발표]" 게시글을 확인해주세요.</p>';
        } else {
          actionHtml = `<div class="event-entry" data-event-id="${ev.id}">` +
            (isLoggedIn()
              ? `<button class="btn btn-primary" type="button" data-event-enter="${ev.id}">응모하기</button>`
              : `<p class="tf-hint">응모하려면 로그인이 필요합니다. <button class="btn btn-ghost" type="button" data-nav="auth">로그인</button></p>`) +
            `</div>`;
        }
      }
      return `<div class="faq-item"><div class="faq-q"><span>${drawBadge}${escapeHtml(ev.title)}</span>${CHEV_SVG}</div>` +
        `<div class="faq-a"><div class="tf-hint">${period}</div>${escapeHtml(ev.content)}${actionHtml}</div></div>`;
    })
    .join("");
}

async function updateEventEntryStates(events) {
  if (!isLoggedIn()) return;
  const pending = events.filter((ev) => ev.is_drawing && !ev.drawn_at);
  await Promise.all(
    pending.map(async (ev) => {
      try {
        const res = await apiFetch(`/api/events/${ev.id}/my-status`);
        if (!res.ok) return;
        const status = await res.json();
        if (!status.entered) return;
        const box = document.querySelector(`.event-entry[data-event-id="${ev.id}"]`);
        if (box) box.innerHTML = '<span class="status-badge ok">이미 응모함</span>';
      } catch {
        /* 상태 조회 실패 시 기본 "응모하기" 버튼 유지 */
      }
    })
  );
}

document.addEventListener("click", async (e) => {
  const enterBtn = e.target.closest("[data-event-enter]");
  if (!enterBtn) return;
  enterBtn.disabled = true;
  try {
    const res = await apiFetch(`/api/events/${enterBtn.dataset.eventEnter}/enter`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "응모에 실패했습니다.");
    const box = enterBtn.closest(".event-entry");
    if (box) box.innerHTML = '<span class="status-badge ok">이미 응모함</span>';
  } catch (err) {
    alert(err.message);
    enterBtn.disabled = false;
  }
});

/* 문의하기 (로그인 필수) */
function updateInquiryView() {
  const logged = isLoggedIn();
  document.getElementById("inquiry-guest").style.display = logged ? "none" : "";
  document.getElementById("inquiry-authed").style.display = logged ? "" : "none";
  if (logged) loadInquiries();
}

let inquiryListPage = 1;

async function loadInquiries(page = 1) {
  inquiryListPage = page;
  try {
    const offset = (page - 1) * BO_MONITOR_PAGE_SIZE;
    const res = await apiFetch(`/api/inquiries?offset=${offset}&limit=${BO_MONITOR_PAGE_SIZE}`);
    if (!res.ok) throw new Error("문의 내역 조회 실패");
    const { inquiries, total } = await res.json();
    const box = document.getElementById("inquiry-list");
    box.innerHTML = inquiries.length
      ? inquiries
          .map(
            (q) =>
              `<div class="document-item"><div class="doc-title">${escapeHtml(q.title)}</div>` +
              `<div class="tf-hint">${fmtDate(q.created_at)}</div><p>${escapeHtml(q.content)}</p></div>`
          )
          .join("")
      : '<p class="support-empty">등록된 문의 내역이 없습니다.</p>';
    renderPagination("inquiry-pagination", page, boMonitorTotalPages(total), "support-inquiries");
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

  /* CSS의 right 계산식은 --maxw(1080px) 기준 중앙 콘텐츠 컬럼을 가정하는데, Backoffice
     (.container-wide, 1440px)는 그 식만으론 표·목록 위에 버튼이 겹쳤다 — 화면이 좁을수록
     실제 여백이 거의 없어서 고정 계산식으로는 폭 차이를 못 따라감. 화면마다 컨테이너 폭이
     다를 수 있으니 아예 폭을 가정하지 않고, 매번 현재 활성 화면의 실제 콘텐츠 오른쪽 끝을
     getBoundingClientRect로 직접 측정해서 그 바깥에 붙인다 — 어떤 폭이든 안전.
     Backoffice는 사이드바 옆 .bo-content가 실제 콘텐츠 폭이라 이걸 우선 쓰고, 없으면
     일반 화면의 .container를 쓴다. */
  const reposition = () => {
    const active = document.querySelector("main > .section.active");
    const content = active?.querySelector(".bo-content") || active?.querySelector(".container");
    if (!content) { btn.style.right = ""; return; }
    // CSS "right"는 뷰포트 오른쪽 끝에서 버튼까지의 거리 — 버튼(46px)+간격(18px)이
    // 콘텐츠 바깥 여백 안에 다 들어가야 하므로 그만큼을 빼야 한다(더했던 게 부호 버그였음:
    // 실측했더니 오히려 콘텐츠 쪽으로 파고들어 표를 덮고 있었음).
    const marginOutsideContent = window.innerWidth - content.getBoundingClientRect().right;
    btn.style.right = Math.max(12, marginOutsideContent - btn.offsetWidth - 18) + "px";
  };

  window.addEventListener("scroll", toggle, { passive: true });
  window.addEventListener("resize", reposition);
  window.addEventListener("scroll", reposition, { passive: true });
  btn.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  toggle();
  reposition();
  // 섹션 전환(navigate) 후 콘텐츠가 바뀌므로 약간의 지연 후 재계산
  window.addEventListener("hashchange", () => setTimeout(reposition, 50));
})();

/* ── 스크롤 유도 힌트: 화면 전환 시 아래에 더 볼 내용이 있으면 노출,
   스크롤을 시작하면 사라진다(uidesign.tips "Prompt User to Scroll") ── */
(function () {
  const hint = document.getElementById("scroll-hint");
  if (!hint) return;
  const hide = () => hint.classList.remove("show");
  window.updateScrollHint = function () {
    hide();
    window.setTimeout(() => {
      if (window.scrollY > 40) return;   // 이미 스크롤된 채로 진입했으면 노출 안 함
      // 사이트 전체 footer가 아니라 "현재 화면 콘텐츠"에 더 볼 게 있는지만 기준으로 삼는다 —
      // document.scrollHeight로 재면 footer 높이만으로도 모든 화면에서 항상 노출돼
      // 고정 위치 힌트가 화면 하단부 실제 콘텐츠 위에 겹쳐 보이는 문제가 있었음.
      const activeSection = document.querySelector("main > .section.active");
      if (!activeSection) return;
      const overflow = activeSection.getBoundingClientRect().bottom - window.innerHeight;
      if (overflow > 160) hint.classList.add("show");
    }, 200);
  };
  window.addEventListener("scroll", hide, { passive: true });
})();

/* ── 은행원 iframe → 부모 SPA 이동 (이체내역 조회하기 등) ───────────── */
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
