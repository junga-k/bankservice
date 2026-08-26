/* 홈 히어로 3D 장식 — 매치뱅크 로고의 "겹치는 두 원(매칭)" 모티프를
   히어로 중앙 전체에 흐르는 은은한 블러 앰비언트로 표현.
   텍스트/버튼 뒤에서 멀어졌다가 만나기를 반복 — 텍스트 가독성을 위해
   투명도를 낮추고 CSS blur로 경계를 흐린다.
   데스크톱 전용 점진적 향상 — 실패/미지원/모바일/reduced-motion이면 조용히
   종료하고 CSS 블롭(.hero::before)만 남는다. */

const container = document.getElementById("hero-3d");

function supportsWebGL() {
  try {
    const canvas = document.createElement("canvas");
    return !!(window.WebGLRenderingContext && canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const isDesktopWidth = window.matchMedia("(min-width: 900px)").matches;
const hasWebGL = supportsWebGL();

// 조건 중 하나라도 안 맞으면 조용히 CSS 블롭 대체로 넘어가던 것이라, 어떤 조건 때문에
// 안 뜨는지 콘솔에서 바로 보이게 진단 로그를 남긴다(2026-08-10, 히어로 애니메이션이
// 사용자 브라우저에서만 안 보인다는 신고로 추가 — 원인 후보: reduced-motion 설정,
// 900px 미만 폭, WebGL 미지원/차단, 또는 아래 unpkg CDN 로드 실패).
console.info("[hero3d] 초기화 조건:", {
  container: !!container, isDesktopWidth, prefersReducedMotion, hasWebGL,
});

if (container && isDesktopWidth && !prefersReducedMotion && hasWebGL) {
  initHero3D(container);
}

/* 컨테이너가 실제 크기를 가질 때까지 기다린다.
   이 스크립트는 로드 직후 실행되는데, SPA 라 그 시점엔 #home 섹션이 아직 표시되기 전이라
   clientWidth/clientHeight 가 0 이다. 그대로 renderer.setSize(0, 0) 을 하면 캔버스가
   0x0 으로 생성돼, 사용자가 창을 리사이즈하기 전까지 3D 히어로가 아예 보이지 않는다
   (2026-08-26 라이브 배포에서 확인 — 첫 화면 좌측이 빈 배경으로 보이던 원인). */
function waitForSize(el, timeoutMs = 10000) {
  return new Promise((resolve) => {
    if (el.clientWidth > 0 && el.clientHeight > 0) { resolve(true); return; }
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      observer.disconnect();
      clearTimeout(timer);
      resolve(ok);
    };
    const observer = new ResizeObserver(() => {
      if (el.clientWidth > 0 && el.clientHeight > 0) finish(true);
    });
    const timer = setTimeout(() => finish(false), timeoutMs);
    observer.observe(el);
  });
}

async function initHero3D(el) {
  let THREE;
  try {
    THREE = await import("three");
  } catch (e) {
    console.warn("[hero3d] three.js CDN(unpkg.com) 로드 실패 — 광고차단기/네트워크 차단을 의심해보세요.", e);
    return; // CDN 로드 실패 — .hero::before 블롭이 자연스러운 대체
  }

  if (!(await waitForSize(el))) {
    console.warn("[hero3d] 컨테이너 크기가 확정되지 않아 초기화를 건너뜁니다 — .hero::before 블롭으로 대체됩니다.");
    return;
  }

  const heroEl = el.closest(".hero") || el;

  const width = el.clientWidth;
  const height = el.clientHeight;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, width / height, 0.1, 100);
  /* 좌우 분할 레이아웃에서 구체 전용 영역(전체 히어로보다 좁음)에 맞춰
     카메라를 더 가까이 당겨 구체가 이전보다 크고 또렷하게 보이도록 함 */
  camera.position.set(0, 0, 7);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(width, height);
  el.appendChild(renderer.domElement);

  const group = new THREE.Group();
  scene.add(group);

  /* 로고의 겹치는 두 원(매칭) — 블러로 경계를 흐려 텍스트 뒤 앰비언트로 */
  const geometry = new THREE.SphereGeometry(1.7, 48, 48);
  /* 진한 그린 히어로 배경 위에서 빛나는 흰 구체 — MeshStandardMaterial(조명 반응형)은
     조명 각도에 따라 회녹색으로 탁하게 보여서, 조명 영향을 받지 않는 MeshBasicMaterial로
     순수한 흰색을 그대로 렌더링한다 */
  const sphereA = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.62 })
  );
  const sphereB = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.68 })
  );
  sphereA.position.z = 0.3;
  sphereB.position.z = -0.3;
  group.add(sphereA, sphereB);

  let raf = null;
  const clock = new THREE.Clock();
  const pointerTarget = { x: 0, y: 0 };

  heroEl.addEventListener("pointermove", (e) => {
    const rect = heroEl.getBoundingClientRect();
    pointerTarget.x = ((e.clientX - rect.left) / rect.width - 0.5) * 2;
    pointerTarget.y = ((e.clientY - rect.top) / rect.height - 0.5) * 2;
  });
  heroEl.addEventListener("pointerleave", () => {
    pointerTarget.x = 0;
    pointerTarget.y = 0;
  });

  function animate() {
    raf = requestAnimationFrame(animate);
    const t = clock.getElapsedTime();

    /* 0(완전히 겹쳐 "매치") ↔ 1.5(떨어짐)를 왕복 — 구체 전용 영역(예전보다 좁은 프레임)에
       맞춰 진폭을 줄여 화면 밖으로 벗어나지 않게 함 */
    const spread = 1.5 * ((Math.sin(t * 0.5) + 1) / 2);
    sphereA.position.x = -spread;
    sphereB.position.x = spread;
    sphereA.position.y = Math.sin(t * 0.6) * 0.35;
    sphereB.position.y = Math.sin(t * 0.6 + Math.PI) * 0.35;

    group.rotation.y = pointerTarget.x * 0.12;
    group.rotation.x = -pointerTarget.y * 0.08;

    renderer.render(scene, camera);
  }

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = null;
  }
  function start() {
    if (!raf) animate();
  }

  /* 화면에서 벗어나면 렌더 루프 정지 — 불필요한 배터리 소모 방지 */
  const visibilityObserver = new IntersectionObserver(
    (entries) => entries.forEach((entry) => (entry.isIntersecting ? start() : stop())),
    { threshold: 0.05 }
  );
  visibilityObserver.observe(el);

  window.addEventListener("resize", () => {
    if (!window.matchMedia("(min-width: 900px)").matches) {
      stop();
      return;
    }
    const w = el.clientWidth;
    const h = el.clientHeight;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  });

  start();
}
