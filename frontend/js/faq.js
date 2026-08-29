/** Native FAQ disclosure enhancement; summary retains browser keyboard behaviour. */
import { MOTION, prefersReducedMotion } from './shared/motion.js';
import { renderText } from './shared/dom.js';

const faqInstances = new WeakMap();

function setExpanded(detail) {
  detail?.querySelector?.('summary')?.setAttribute?.('aria-expanded', String(Boolean(detail.open)));
  detail?.summary?.setAttribute?.('aria-expanded', String(Boolean(detail.open)));
}

export function createFaqController(details, { current, hint, progress } = {}) {
  const list = [...details];
  const paint = (active) => {
    const index = list.indexOf(active);
    if (current) renderText(current, index < 0 ? '00' : String(index + 1).padStart(2, '0'));
    if (hint) renderText(hint, active?.dataset?.faqLabel || 'Выберите вопрос');
    if (progress?.style) {
      progress.style.width =
        index < 0 || !list.length ? '0%' : `${((index + 1) / list.length) * 100}%`;
    }
    list.forEach(setExpanded);
  };
  return {
    paint,
    sync(active) {
      list.forEach((detail) => {
        const shouldOpen = detail === active;
        if (detail.open !== shouldOpen) detail.open = shouldOpen;
      });
      paint(active);
    },
  };
}

export function initFaq({
  document: doc = globalThis.document,
  reducedMotion = prefersReducedMotion,
} = {}) {
  if (!doc) return null;
  const existing = faqInstances.get(doc);
  if (existing) return existing;

  const details = [...(doc.querySelectorAll?.('#lpFaq .lp-q') || [])];
  if (!details.length) return null;
  const faq = createFaqController(details, {
    current: doc.getElementById?.('faqCurrent'),
    hint: doc.getElementById?.('faqHint'),
    progress: doc.getElementById?.('faqProgress'),
  });
  const animations = new WeakMap();
  const listeners = new Map();

  const animateOpen = (detail) => {
    if (!detail.open || reducedMotion()) return;
    const panel = detail.querySelector?.(':scope > p') ?? detail.querySelector?.('p');
    if (!panel || typeof panel.animate !== 'function') return;
    animations.get(detail)?.cancel?.();
    panel.style?.setProperty?.('overflow', 'hidden');
    const animation = panel.animate([{ height: '0px' }, { height: `${panel.scrollHeight}px` }], {
      duration: MOTION.reveal,
      easing: MOTION.easing,
    });
    animations.set(detail, animation);
    animation?.finished
      ?.catch?.(() => {})
      .finally?.(() => panel.style?.removeProperty?.('overflow'));
  };

  details.forEach((detail) => {
    const listener = () => {
      if (detail.open) {
        details.forEach((other) => {
          if (other !== detail && other.open) other.open = false;
        });
        faq.paint(detail);
        animateOpen(detail);
        return;
      }
      faq.paint(details.find((item) => item.open) || null);
    };
    detail.addEventListener('toggle', listener);
    listeners.set(detail, listener);
  });
  faq.paint(details.find((detail) => detail.open) || null);

  const instance = {
    ...faq,
    destroy() {
      listeners.forEach((listener, detail) => {
        detail.removeEventListener?.('toggle', listener);
        animations.get(detail)?.cancel?.();
      });
      listeners.clear();
      if (faqInstances.get(doc) === instance) faqInstances.delete(doc);
    },
  };
  faqInstances.set(doc, instance);
  return instance;
}
