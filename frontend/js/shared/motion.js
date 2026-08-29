export const MOTION = Object.freeze({
  fast: 250,
  normal: 360,
  slow: 480,
  reveal: 420,
  stagger: 70,
  dialog: 320,
  price: 400,
  metrics: 1000,
  easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
});

export function prefersReducedMotion(win = globalThis.window) {
  try {
    return Boolean(win?.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
  } catch (_) {
    return false;
  }
}

export function createNumberTween({ requestAnimationFrame, cancelAnimationFrame } = {}) {
  const request = requestAnimationFrame ?? globalThis.requestAnimationFrame;
  const cancel = cancelAnimationFrame ?? globalThis.cancelAnimationFrame;
  let frame = null;
  let run = 0;

  return ({
    from = 0,
    to,
    duration = MOTION.price,
    onUpdate,
    reducedMotion = prefersReducedMotion(),
  }) => {
    const currentRun = ++run;
    if (frame !== null && cancel) cancel(frame);
    frame = null;

    if (reducedMotion || typeof request !== 'function' || duration <= 0) {
      onUpdate(to);
      return () => {};
    }

    let startedAt = null;
    const tick = (timestamp) => {
      if (run !== currentRun) return;
      if (startedAt === null) startedAt = timestamp;
      const progress = Math.min((timestamp - startedAt) / duration, 1);
      if (progress === 1) {
        frame = null;
        onUpdate(to);
        return;
      }
      onUpdate(from + (to - from) * progress);
      frame = request(tick);
    };

    frame = request(tick);
    return () => {
      if (run !== currentRun) return;
      if (frame !== null && cancel) cancel(frame);
      frame = null;
      run += 1;
    };
  };
}

export const tweenNumber = createNumberTween();
