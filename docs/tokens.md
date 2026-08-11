# 매치뱅크 디자인 토큰 — 브라우저 실측 추출 (2026-07-27)

추출 방법: `http://localhost:8000`을 Playwright로 열어 `getComputedStyle()`로 직접 읽음(파일 grep 아님). 색상은 브라우저가 실제로 계산한 `rgb()`/`rgba()` 값을 hex로 변환해 표기. **적용은 아직 안 함 — 확인 후 지시해줘.**

---

## 1. 색상 (Color)

### 1-0. Figma의 Primitives 레이어에 관한 참고 (2026-07-30 추가)

Figma 파일엔 아래 Semantic 값들 밑에 `Primitives` 컬렉션(`green/100~700`, `gray/0~700`, `amber/100~700`, `red/100~600`, `blue/100~600` 등 원시 팔레트)이 별도로 존재하고, Semantic 변수들이 이를 alias한다. **이 Primitives 레이어는 Figma 변수 바인딩 구조를 위한 내부 구현 디테일이며, 실제 웹사이트 코드에는 대응하는 개념이 없다**(`:root` CSS는 애초에 `--blue`/`--text` 같은 시맨틱 이름만 쓰고 원시 팔레트 변수 자체가 없음). 코드 대조·실측 검증은 항상 아래 Semantic 레이어(`color/brand/primary` 등) 값 기준으로 할 것 — Primitives 값과 코드를 직접 비교하지 말 것.

### 1-1. `:root` CSS 커스텀 프로퍼티 (전부 실측)

| 토큰 | 값 |
|---|---|
| `--blue` (brand primary) | `#0FA968` |
| `--blue-dark` (hover/강조) | `#0B8457` |
| `--blue-soft` (연한 배경) | `#E3F6EC` |
| `--blue-line` (연한 테두리) | `#A8E0C4` |
| `--bg` | `#FFFFFF` |
| `--bg-soft` | `#F8FAFD` |
| `--border` | `#DDE3EA` |
| `--text` | `#3C4043` |
| `--text-sub` | `#5F6368` |
| `--success` | `#0FA968` (=`--blue`) |
| `--success-soft` | `#E3F6EC` (=`--blue-soft`) |
| `--warning` | `#B45309` |
| `--warning-soft` | `#FEF3E2` |
| `--error` | `#DC2626` |
| `--error-soft` | `#FDECEC` |
| `--info` | `#2563EB` |
| `--info-soft` | `#EAF1FD` |

### 1-2. `.trend-badge` 색상 — ~~토큰 밖 하드코딩~~ **[2026-07-28 재측정] 코드 수정 반영되어 현재는 토큰과 일치**

> 이 표는 원래 코드 수정 전(하드코딩 상태)의 기록이었음. 2026-07-28 세션에 `site/css/style.css:1323-1324`를 시맨틱 토큰 참조로 고친 뒤, 같은 날 다시 Playwright로 Backoffice 대시보드에서 실측 재확인함(아래 값은 이번 재측정 결과, `getComputedStyle` 그대로).

| 위치 | 배경(실측) | 텍스트(실측) | 비고 |
|---|---|---|---|
| `.trend-badge.up` | `rgb(227,246,236)` = `#E3F6EC` | `rgb(11,132,87)` = `#0B8457` | `--success-soft`/`--blue-dark`와 **정확히 일치**(과거엔 `#E6F4EA`/`#1E7E34`로 달랐으나 수정됨) |
| `.trend-badge.down` | `rgb(253,236,236)` = `#FDECEC` | `rgb(220,38,38)` = `#DC2626` | `--error-soft`/`--error`와 **정확히 일치**(과거엔 `#FCE8E6`/`#C5221F`로 달랐으나 수정됨) |
| `.trend-badge.flat` | `--bg-soft` | `--text-sub` | 토큰 그대로 사용(변경 없었음) |

### 1-3. 상태별 실측 (버튼/뱃지)

| 엘리먼트 | 배경 | 텍스트 | 테두리 |
|---|---|---|---|
| `.btn-primary` | `#0FA968` | `#FFFFFF` | 없음(투명) |
| `.btn-primary` hover | `#0B8457` | `#FFFFFF` | — |
| `.btn-ghost` | `#FFFFFF` | `#0FA968` (62개 인스턴스 전수 재확인, 예외 없음)* | `#A8E0C4` |
| `#home .btn-primary` (히어로 예외) | `#FFFFFF` | `#0B8457` | — |
| `.status-badge.ok` | `#E3F6EC` | `#0B8457` | 없음 |
| `.cat-tab.active` | `#0FA968` | `#FFFFFF` | `#0FA968` |
| `.bo-tab.active` | `#E3F6EC` | `#0B8457` | 없음 |

\* **[2026-07-28 재측정 — 정정]** 위 각주는 잘못된 관측이었음. DOM에 존재하는 `.btn-ghost` 인스턴스 62개(홈 3 / 내계좌 2 / 로그인·아이디찾기·비밀번호찾기·회원가입 9 / Backoffice 43 / 고객센터 7, 5개 섹션 전체)를 하나씩 `getComputedStyle`로 개별 측정한 결과 **62/62 전부 `rgb(15,169,104)` = `#0FA968` = `--blue`**, `--blue-dark`는 단 한 곳도 없었음. CSS 소스에도 `.btn-ghost{color:var(--blue)}` 규칙이 유일하고(`:hover`는 배경만 변경, 텍스트색 불변), 다른 어떤 셀렉터도 `.btn-ghost` 텍스트색을 오버라이드하지 않음(grep 재확인). 컨텍스트 무관하게 항상 `--blue` — 확인 완료, 더 이상 미결 아님.

---

## 2. Spacing 스케일

`:root`에 8px 기준 스케일이 실제로 정의돼 있음 (실측):

| 토큰 | 값 |
|---|---|
| `--space-1` | `4px` |
| `--space-2` | `8px` |
| `--space-3` | `12px` |
| `--space-4` | `16px` |
| `--space-5` | `24px` |
| `--space-6` | `32px` |
| `--space-7` | `48px` |
| `--space-8` | `64px` |
| `--space-9` | `96px` |

### 실제 컴포넌트에서 관찰된 padding/gap 조합 (getComputedStyle 실측)

| 엘리먼트 | padding | gap |
|---|---|---|
| `.btn` (일반) | `13px 32px` | `8px` |
| `.bo-content .btn` (Backoffice 축소형) | `8px 16px` | `8px` |
| `.quick-tile` | `16px 12px` | `8px` |
| `.cat-tab` | `12px 24px` | — |
| `.product-sort` (select) | `0px 34px 0px 12px` | — |
| `#product-search input` | `0px 12px 0px 40px` | — |
| `.panel.auth-panel` (로그인 카드) | `24px` | — |
| `.bo-tab` | `10px 14px` | — |
| `.admin-table th` | `10px 12px` | — |
| `.top-pick` (TOP추천 카드) | `24px` | `16px` |

레이아웃 폭 토큰: `--maxw: 1080px`(`.container` 최대폭), `--header-h: 64px`.

---

## 3. Border-radius 스케일

| 토큰 | 값 | 실제 쓰이는 곳(실측) |
|---|---|---|
| `--radius-sm` | `8px` | `.btn`(일반), Select/입력창, `.nav a`, `.bo-tab` |
| `--radius` | `14px` | `.card`(범용 카드 클래스) → `#account`(내 계좌) 계좌 카드 그리드가 재정의 없이 이 값을 그대로 상속해서 씀(`getComputedStyle`로 재확인: `borderRadius: "14px"`). `.modal-box`(팝업), `.mp-acct`(마이페이지 계좌 카드)도 동일 토큰 사용 |
| `--radius-lg` | `20px` | `.quick-tile`, `.panel`, `.top-pick`, Home 카드류 |
| `--radius-pill` | `999px` | `#home .btn`(히어로 CTA 예외), `.cat-tab`, `.status-badge`, `.bo-tab`은 **8px**(pill 아님, 주의) |
| (하드코딩) | `10px` | `.trend-badge` — 토큰에 없는 값, pill이 아니라 10px 고정 |

---

## 4. Typography 스케일

### 4-1. `:root` font-size 스케일 (실측)

| 토큰 | 값 |
|---|---|
| `--text-xs` | `12px` |
| `--text-sm` | `14px` |
| `--text-md` | `16px` |
| `--text-lg` | `18px` |
| `--text-xl` | `22px` |
| `--text-2xl` | `28px` |
| `--text-3xl` | `40px` |

폰트 패밀리: `--font-body: "IBM Plex Sans KR", -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif` / `--font-display: "Cafe24 Ssurround", sans-serif`(2026-08-12부로 Black Han Sans에서 교체 — "매치뱅크" 4글자만 쓰는 서브셋을 `site/fonts/`에 자체 호스팅)

### 4-2. 실제 엘리먼트별 typography (getComputedStyle 실측, weight/line-height/letter-spacing 포함)

| 엘리먼트 | font-size | line-height | font-weight | letter-spacing |
|---|---|---|---|---|
| `.hero h1` | 36px | 48.6px(=1.35배) | 800 | -0.5px |
| `.hero p` | 18px | normal | 400 | normal |
| `.section-title` / `.showcase-text h3` | 28px | normal | 700 | normal |
| `.showcase-text p` | 14px | 23.8px(=1.7배) | 400 | normal |
| `.quick-tile-label` | 14px | normal | 600 | normal |
| `nav a` (데스크톱 폭 기준, clamp 최댓값) | 16px | normal | 600(활성)/500(기본, 별도 확인 필요) | normal |
| `.brand`(로고 워드마크, Black Han Sans) | 22px | normal | 400 | normal(자간은 `.auth-aside-brand-text`류만 2px 별도 지정, 헤더 로고엔 없음) |
| `.auth-heading`(로그인 제목) | 22px | normal | 700 | normal |
| `.tf-field label` | 12px | normal | 600 | normal |
| `.metric .value` (일반, 예: 이용통계 카드) | 34px | — | 800 | — |
| `.metric .value` (`#bo-dash-summary`/`#bo-transfer-summary`/`#bo-security-summary` 한정 축소) | **22px** | 1.2(transfer만 명시) | 800 | — |
| `.metric .label` | 14px | — | 500 | — |
| `.rank-name` | 13px | — | 500 | — |
| `.rank-val` | 13px | — | 400 | — |
| `.rank-pct` | 12px | — | 400 | — |
| `.admin-table th` | 13px | — | 600 | — |
| `.status-badge` (health-strip, `.lg` modifier로 추정) | 13px | — | 700 | — |
| `.trend-badge` | 12px | — | 700 | — |
| `.bo-tab` | 14px | — | 700 | — |

**주의**: `.metric .value`는 스케일 토큰(34px)이 기본이지만 대시보드/이체모니터링/보안이벤트 요약 3곳에서만 `22px`로 오버라이드됨 — "같은 컴포넌트의 다른 값"이 아니라 실제로 CSS가 컨텍스트별로 다르게 정의한 것. Figma Stat Card 컴포넌트는 현재 34px(일반형) 기준으로만 맞춰져 있고, 이 압축형(22px, 좌측정렬, sparkline 포함)은 별도 variant로 아직 없음.

---

## 5. Shadow / Elevation

| 토큰 | 값(실측) |
|---|---|
| `--shadow-sm` | `0 2px 8px rgba(60, 64, 67, 0.06)` |
| `--shadow-md` | `0 8px 24px rgba(60, 64, 67, 0.08)` |
| `--shadow-lg` | `0 16px 40px rgba(60, 64, 67, 0.14)` |
| `--shadow-brand` | `0 4px 14px rgba(15, 169, 104, 0.28)` |

실제 사용 확인:
- `.quick-tile` → `--shadow-sm`
- `.panel.auth-panel`(로그인 카드) → `--shadow-lg`
- `.btn-primary` / `.top-pick`(TOP추천) → `--shadow-brand`
- `.event-banner` → 토큰 아닌 커스텀: `0 0 0 1px var(--border), 0 1px 2px rgba(0,0,0,0.04)` (링 테두리 + 옅은 그림자 조합)

---

## 6. Motion (보너스 — 애니메이션에도 쓰이므로 함께 기록)

| 토큰 | 값 |
|---|---|
| `--dur-fast` | `150ms` |
| `--dur-base` | `200ms` |
| `--dur-slow` | `300ms` |
| `--ease` | `cubic-bezier(0.4, 0, 0.2, 1)` |

---

## 7. 실측 중 발견한 특이사항 — 판단 결과 (2026-07-28 확정)

1. ✅ **완료 — 코드 통일**. `.trend-badge` 하드코딩 색상(`#E6F4EA`/`#1E7E34`, `#FCE8E6`/`#C5221F`)을 시맨틱 토큰으로 교체: `site/css/style.css:1323-1324`를 `background: var(--success-soft); color: var(--blue-dark);` / `background: var(--error-soft); color: var(--error);`로 수정. 브라우저 재실측으로 반영 확인 완료.
2. ✅ **완료 — Figma 추가**. Stat Card 컴포넌트에 `Size=Compact`(22px, 좌측정렬, trend-badge 11px/1px·6px) variant 신설 — `#bo-transfer-summary`/`#bo-security-summary` 실사용 값 그대로 반영. 스크린샷 검증 완료.
3. ✅ **완료 — Figma 추가**. Status Badge 컴포넌트에 `Size=Large`(6px/14px, 13px) variant 신설 — `.status-badge.lg` 실사용 값 그대로 반영. 스크린샷 검증 완료.
4. ✅ **완료 — Figma 추가**. Card 컴포넌트에 `Family=AccountCard`(14px radius, 16px/24px padding, rest=shadow-sm/hover=shadow-md) variant 신설 — 기존 `Family=QuickTile`(20px)과 별도 스케일로 분리. 스크린샷 검증 완료.
5. **보류 — 그대로 유지**. `.bo-tab`(8px, 사각형)과 `.cat-tab`/`.support-tab`/`.account-tab`(pill)의 차이는 의도적 구분(백오피스 사이드바 메뉴 vs 공개 화면 필터 칩)으로 판단 — 코드/컴포넌트 변경 없음. 실측값(패딩 10px/14px, radius 8px)만 이 문서에 기록해두고, 별도 Figma 컴포넌트로 만들지는 추후 재검토.
6. 로그인 카드 실측 폭 **472px** — 이전 세션에 손계산으로 유추했던 값과 정확히 일치(교차 검증 완료, 참고용 정보로 유지).

---

1~4번은 Figma 반영 완료(컴포넌트 노드ID는 `/tmp/dsb-state-matchbank-001.json`의 `componentVariantsAdded2026_07_28` 참고). 5번은 보류 상태로 남겨둠.

---

## 8. 3개 항목 재검증 (2026-07-28, 같은 세션 내 재측정)

사용자 요청으로 이전 기록을 의심 없이 받아들이지 않고 Playwright(`localhost:8000`, admin 로그인)로 3개 항목을 직접 재측정하고 기존 기록과 대조:

| 항목 | 결과 | 비고 |
|---|---|---|
| `.trend-badge.up` 실제 색상 | **불일치 발견 → 문서 수정** | 1-2절 표가 코드 수정 전(하드코딩) 값인 채로 방치돼 있었음. 재측정값은 `--success-soft`(`#E3F6EC`)/`--blue-dark`(`#0B8457`)와 정확히 일치 — 즉 코드 수정은 이미 반영됐는데 이 문서만 안 따라갔던 것. 위 1-2절 갱신함 |
| `.metric .value` 34px/22px 분기 | **일치 확인** | `#bo-dash-summary`/`#bo-transfer-summary`/`#bo-security-summary` 3개 컨테이너 ID만 22px, `#bo-user-summary`(baseline) 등 나머지는 34px — 기존 기록 그대로 정확 |
| `.btn-ghost` 텍스트 색상 | **불일치 발견(각주가 틀렸음) → 문서 수정** | 62개 인스턴스 전수 조사 결과 예외 없이 `--blue`(`#0FA968`). 과거 각주의 "컨텍스트별로 다르다"는 관측은 재현되지 않음 — 위 1-3절 각주 갱신함 |

교훈: 표 형태로 한 번 기록된 값도 코드가 나중에 바뀌면 조용히 stale해질 수 있다 — 특히 "코드 수정 완료" 이벤트(7절)가 있었다면, 그 수정이 참조하는 실측 표(1-2절 등)도 같이 갱신해야 했는데 이번에 그 갭이 있었음을 확인.
