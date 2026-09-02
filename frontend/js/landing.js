function clamp01(value) {
  return Math.min(1, Math.max(0, Number(value) || 0));
}

function finaleEase(value) {
  const progress = clamp01(value);
  return 1 - (1 - progress) ** 3;
}

/** Paint the four-act finale without reading layout, so reduced motion stays deterministic. */
export function renderFinaleProgress(elements, progress, reducedMotion = false) {
  const t = reducedMotion ? 1 : clamp01(progress);
  const letters = elements?.letters || [];

  letters.forEach((letter, index) => {
    const enter = finaleEase(clamp01((t - 0.02 - index * 0.026) / 0.24));
    letter.style.opacity = String(clamp01(enter * 1.6));
    letter.style.transform = `translate3d(0, ${(1 - enter) * 112}%, 0) rotate(${(1 - enter) * (index % 2 ? 5 : -5)}deg)`;
  });

  const fly = finaleEase(clamp01((t - 0.36) / 0.22));
  const hand = finaleEase(clamp01((t - 0.46) / 0.28));
  const letter = finaleEase(clamp01((t - 0.62) / 0.2));
  const login = finaleEase(clamp01((t - 0.72) / 0.14));

  elements.wordClip.style.opacity = reducedMotion ? '0' : '1';
  elements.wordClip.style.transform = reducedMotion
    ? 'none'
    : `translate3d(0, ${(fly * 116).toFixed(2)}vh, 0)`;
  elements.ghost.style.opacity = String(hand);
  elements.hand.style.opacity = String(hand);
  elements.hand.style.transform = `translate3d(-46%, ${(1 - hand) * 16}%, 0) rotate(${(1 - hand) * 5}deg) scale(${0.94 + hand * 0.06})`;
  elements.letter.style.opacity = String(letter);
  elements.letter.style.transform = `translate3d(-50%, ${(1 - letter) * -54}px, 0)`;
  elements.login.style.opacity = String(login);
  elements.login.style.transform = `translate3d(-50%, ${(1 - login) * -14}px, 0)`;
}

export function initFinale(options = {}) {
  const doc = options.document ?? globalThis.document;
  const win = options.window ?? globalThis.window;
  const finale = doc?.getElementById?.('zephyrFinale');
  if (!finale) return { destroy() {} };

  const motionQuery = resolveMotionQuery(win, options.motionQuery);
  const reducedMotion =
    options.reducedMotion ?? (() => Boolean(motionQuery?.matches) || prefersReducedMotion(win));
  const elements = {
    finale,
    ghost: finale.querySelector('.zp-final-ghost'),
    hand: doc.getElementById('zpFinalHand'),
    letter: doc.getElementById('zpFinalLetter'),
    login: finale.querySelector('.zp-final-login'),
    wordClip: finale.querySelector('.zp-final-word-clip'),
    letters: [...finale.querySelectorAll('.zp-final-word span')],
  };
  if (Object.values(elements).some((value) => !value)) return { destroy() {} };

  const request = options.requestAnimationFrame ?? win?.requestAnimationFrame?.bind(win);
  const cancelFrame = options.cancelAnimationFrame ?? win?.cancelAnimationFrame?.bind(win);
  let frame = null;
  const draw = () => {
    frame = null;
    const rect = finale.getBoundingClientRect();
    const viewport = Math.max(1, Number(win?.innerHeight) || 1);
    const travel = Math.max(1, rect.height - viewport);
    renderFinaleProgress(elements, -rect.top / travel, Boolean(reducedMotion()));
  };
  const schedule = () => {
    if (frame !== null) return;
    if (typeof request === 'function') frame = request(draw);
    else draw();
  };
  win?.addEventListener?.('scroll', schedule, { passive: true });
  win?.addEventListener?.('resize', schedule);
  motionQuery?.addEventListener?.('change', schedule);
  draw();

  return {
    destroy() {
      if (frame !== null && typeof cancelFrame === 'function') cancelFrame(frame);
      win?.removeEventListener?.('scroll', schedule);
      win?.removeEventListener?.('resize', schedule);
      motionQuery?.removeEventListener?.('change', schedule);
    },
  };
}

/** Public landing metrics and motion. Keeps product data rendering text-only. */
import { createNumberTween, MOTION, prefersReducedMotion } from './shared/motion.js';
import { renderText } from './shared/dom.js';
import { railDone, railStart } from './shared/topbar.js';

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';
const REVEAL_SELECTOR = ['#loginScreen .rv'].join(',');

function addClass(node, name) {
  node?.classList?.add?.(name);
}

function putText(node, value) {
  if (node) renderText(node, value);
}

function validMetric(value) {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

/* Совокупная аудитория каналов — это миллионы, и полное число («24 800 000»)
   не влезает в карточку метрики. Для таких счётчиков разметка помечается
   data-public-stat-compact, и число сокращается до «24,8 млн». */
function compactText(value) {
  const n = Math.round(value);
  if (n >= 1e9) return `${(n / 1e9).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} млрд`;
  if (n >= 1e6) return `${(n / 1e6).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} млн`;
  if (n >= 1e4) return `${(n / 1e3).toLocaleString('ru-RU', { maximumFractionDigits: 0 })} тыс`;
  return n.toLocaleString('ru-RU');
}

function metricText(value, node) {
  if (node?.dataset?.publicStatCompact !== undefined) return compactText(value);
  return Math.round(value).toLocaleString('ru-RU');
}

function resolveMotionQuery(win, provided) {
  if (provided !== undefined) return provided;
  try {
    return win?.matchMedia?.(REDUCED_MOTION_QUERY) ?? null;
  } catch (_) {
    return null;
  }
}

export function createPublicStatsLoader(options = {}) {
  const doc = options.document ?? globalThis.document;
  const win = options.window ?? globalThis.window;
  const fetcher = options.fetch ?? globalThis.fetch;
  const queryAll = options.queryAll ?? ((selector) => doc?.querySelectorAll?.(selector) || []);
  const queryOne = options.queryOne ?? ((selector) => doc?.querySelector?.(selector));
  const request = options.requestAnimationFrame ?? globalThis.requestAnimationFrame;
  const cancelFrame = options.cancelAnimationFrame ?? globalThis.cancelAnimationFrame;
  const motionQuery = resolveMotionQuery(win, options.motionQuery);
  const reducedMotion =
    options.reducedMotion ?? (() => Boolean(motionQuery?.matches) || prefersReducedMotion(win));
  const createTween = () =>
    options.tween ??
    createNumberTween({ requestAnimationFrame: request, cancelAnimationFrame: cancelFrame });

  let cancels = [];
  let generation = 0;
  let targets = [];
  let destroyed = false;

  const cancel = () => {
    cancels.forEach((stop) => stop?.());
    cancels = [];
  };
  const renderTargets = () => {
    targets.forEach(({ node, target }) => putText(node, metricText(target, node)));
  };
  const handleMotionChange = (event) => {
    if (!event?.matches || !targets.length) return;
    cancel();
    renderTargets();
  };
  motionQuery?.addEventListener?.('change', handleMotionChange);

  const load = async () => {
    const run = ++generation;
    cancel();
    targets = [];
    const nodes = [...queryAll('[data-public-stat]')];
    const status = queryOne('#publicStatsStatus');
    if (!nodes.length || destroyed) return;

    /* v48: пока цифры едут — скелеты вместо пустоты. Ширину держит
       плейсхолдер-текст под классом .skl (он прозрачный). */
    nodes.forEach((node) => {
      node.classList?.add?.('skl');
      if (!String(node.textContent || '').trim()) putText(node, '00 000');
    });
    railStart();
    try {
      const response = await fetcher('/api/public/stats', { credentials: 'same-origin' });
      if (!response.ok) throw new Error('Public stats unavailable');
      const stats = await response.json();
      if (destroyed || run !== generation) return;
      const values = nodes.map((node) => validMetric(stats?.[node.dataset.publicStat]));
      if (values.some((value) => value === null)) throw new Error('Invalid public stats');
      nodes.forEach((node) => node.classList?.remove?.('skl'));
      railDone();
      targets = nodes.map((node, index) => ({ node, target: values[index] }));

      if (reducedMotion()) renderTargets();
      else {
        targets.forEach(({ node, target }) => {
          const tween = createTween();
          const stop = tween({
            duration: MOTION.metrics,
            from: 0,
            onUpdate: (value) => putText(node, metricText(value, node)),
            reducedMotion: false,
            to: target,
          });
          cancels.push(stop);
        });
      }
      putText(status, 'фактические данные рабочего сервиса');
    } catch (_) {
      railDone();
      if (destroyed || run !== generation) return;
      cancel();
      targets = [];
      nodes.forEach((node) => {
        node.classList?.remove?.('skl');
        putText(node, '—');
      });
      putText(status, 'Данные временно недоступны');
    }
  };

  load.cancel = cancel;
  load.destroy = () => {
    if (destroyed) return;
    destroyed = true;
    generation += 1;
    cancel();
    targets = [];
    motionQuery?.removeEventListener?.('change', handleMotionChange);
  };
  return load;
}

export function initLandingDynamics(options = {}) {
  const doc = options.document ?? globalThis.document;
  const win = options.window ?? globalThis.window;
  const request = options.requestAnimationFrame ?? globalThis.requestAnimationFrame;
  const cancelFrame = options.cancelAnimationFrame ?? globalThis.cancelAnimationFrame;
  const Observer = options.IntersectionObserver ?? win?.IntersectionObserver;
  const motionQuery = resolveMotionQuery(win, options.motionQuery);
  const reducedMotion =
    options.reducedMotion ?? (() => Boolean(motionQuery?.matches) || prefersReducedMotion(win));
  const screen = doc?.getElementById?.('loginScreen');
  if (!screen) {
    return {
      destroy() {},
      duration: MOTION.reveal,
      parallaxEnabled: false,
      stagger: MOTION.stagger,
    };
  }

  const items = [
    ...(screen.querySelectorAll?.(REVEAL_SELECTOR) ||
      doc?.querySelectorAll?.(REVEAL_SELECTOR) ||
      []),
  ];
  let reduced = Boolean(reducedMotion());
  let observer = null;
  const revealAll = () => items.forEach((item) => addClass(item, 'is-inview'));
  items.forEach((item, index) => {
    addClass(item, 'zd-reveal');
    item.style?.setProperty?.('--zd-reveal-delay', `${Math.min(index % 4, 3) * MOTION.stagger}ms`);
  });

  if (reduced || typeof Observer !== 'function') revealAll();
  else {
    observer = new Observer(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          addClass(entry.target, 'is-inview');
          observer?.unobserve?.(entry.target);
        });
      },
      { root: screen, rootMargin: '0px 0px -8% 0px', threshold: 0.12 },
    );
    items.forEach((item) => observer.observe(item));
  }
  addClass(screen, 'zd-motion-ready');

  const stage = screen.querySelector?.('.zp-phone-stage');
  const motionTarget = stage;
  const finePointer =
    options.finePointer ?? Boolean(win?.matchMedia?.('(hover:hover) and (pointer:fine)')?.matches);
  let pointerFrame = null;
  let drawFrame = null;
  const resetParallax = () => {
    if (pointerFrame !== null && typeof cancelFrame === 'function') cancelFrame(pointerFrame);
    pointerFrame = null;
    ['--zp-pointer-x', '--zp-pointer-y', '--zp-shift-x', '--zp-shift-y'].forEach((name) =>
      motionTarget?.style?.removeProperty?.(name),
    );
  };
  const onPointerMove = (event) => {
    if (reduced || pointerFrame !== null || typeof request !== 'function') return;
    pointerFrame = request(() => {
      pointerFrame = null;
      if (reduced) return;
      const box = stage.getBoundingClientRect();
      if (!box.width || !box.height) return;
      const x = ((event.clientX - box.left) / box.width - 0.5) * 2;
      const y = ((event.clientY - box.top) / box.height - 0.5) * 2;
      motionTarget.style.setProperty('--zp-pointer-x', `${(x * 2.2).toFixed(2)}deg`);
      motionTarget.style.setProperty('--zp-pointer-y', `${(-y * 1.7).toFixed(2)}deg`);
      motionTarget.style.setProperty('--zp-shift-x', `${(x * 3).toFixed(2)}px`);
      motionTarget.style.setProperty('--zp-shift-y', `${(y * 3).toFixed(2)}px`);
    });
  };
  if (!reduced && stage && finePointer) {
    stage.addEventListener('pointermove', onPointerMove, { passive: true });
    stage.addEventListener('pointerleave', resetParallax);
  }

  if (reduced || typeof request !== 'function') addClass(stage, 'is-drawn');
  else drawFrame = request(() => addClass(stage, 'is-drawn'));

  const handleMotionChange = (event) => {
    reduced = Boolean(event?.matches);
    if (!reduced) return;
    observer?.disconnect?.();
    observer = null;
    revealAll();
    resetParallax();
    addClass(stage, 'is-drawn');
  };
  motionQuery?.addEventListener?.('change', handleMotionChange);

  return {
    destroy() {
      observer?.disconnect?.();
      if (drawFrame !== null && typeof cancelFrame === 'function') cancelFrame(drawFrame);
      resetParallax();
      stage?.removeEventListener?.('pointermove', onPointerMove);
      stage?.removeEventListener?.('pointerleave', resetParallax);
      motionQuery?.removeEventListener?.('change', handleMotionChange);
    },
    duration: MOTION.reveal,
    parallaxEnabled: !reduced && finePointer,
    stagger: MOTION.stagger,
  };
}

export function initLanding(options = {}) {
  const doc = options.document ?? globalThis.document;
  const win = options.window ?? globalThis.window;
  const motionQuery = resolveMotionQuery(win, options.motionQuery);
  const reducedMotion =
    options.reducedMotion ?? (() => Boolean(motionQuery?.matches) || prefersReducedMotion(win));
  const shared = { ...options, document: doc, motionQuery, reducedMotion, window: win };
  putText(
    doc?.getElementById?.('landingTypeWord'),
    'Поиск, проверка контакта и отправка — в одном процессе.',
  );
  const load = createPublicStatsLoader(shared);
  void load();
  const dynamics = initLandingDynamics(shared);
  const finale = initFinale(shared);
  return {
    destroy() {
      load.destroy();
      dynamics.destroy();
      finale.destroy();
    },
    dynamics,
    finale,
    load,
  };
}
