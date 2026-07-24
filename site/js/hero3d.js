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

if (container && isDesktopWidth && !prefersReducedMotion && supportsWebGL()) {
  initHero3D(container);
}

async function initHero3D(el) {
  let THREE;
  try {
    THREE = await import("three");
  } catch {
    return; // CDN 로드 실패 — .hero::before 블롭이 자연스러운 대체
  }

  const heroEl = el.closest(".hero") || el;

  const width = el.clientWidth;
  const height = el.clientHeight;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, width / height, 0.1, 100);
  camera.position.set(0, 0, 9);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(width, height);
  el.appendChild(renderer.domElement);

  scene.add(new THREE.AmbientLight(0xffffff, 0.8));
  const key = new THREE.DirectionalLight(0xffffff, 0.7);
  key.position.set(2, 3, 5);
  scene.add(key);

  const group = new THREE.Group();
  scene.add(group);

  /* 로고의 겹치는 두 원(매칭) — 블러로 경계를 흐려 텍스트 뒤 앰비언트로 */
  const geometry = new THREE.SphereGeometry(1.5, 48, 48);
  /* 밝지만 채도 높은 비비드 민트그린 — 파스텔로 죽지 않으면서도 텍스트 가독성 유지 */
  const sphereA = new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({ color: 0x2ed9a0, transparent: true, opacity: 0.4, roughness: 0.5 })
  );
  const sphereB = new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({ color: 0x5ee6b8, transparent: true, opacity: 0.45, roughness: 0.5 })
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

    /* 0(완전히 겹쳐 "매치") ↔ 2.6(멀리 떨어짐)을 느리게 왕복 */
    const spread = 2.6 * ((Math.sin(t * 0.22) + 1) / 2);
    sphereA.position.x = -spread;
    sphereB.position.x = spread;
    sphereA.position.y = Math.sin(t * 0.35) * 0.3;
    sphereB.position.y = Math.sin(t * 0.35 + Math.PI) * 0.3;

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
