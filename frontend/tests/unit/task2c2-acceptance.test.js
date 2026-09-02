import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { initFaq } from '../../js/faq.js';
import { createPublicStatsLoader, initLandingDynamics } from '../../js/landing.js';
import { bindDialogOpeners } from '../../js/shared/dialogs.js';

function stat(name) {
  return { dataset: { publicStat: name }, textContent: '' };
}

test('metrics tween uses injected rAF for an exact 1000ms intermediate and final value', async () => {
  const node = stat('channels');
  const status = { textContent: '' };
  const frames = [];
  const loader = createPublicStatsLoader({
    cancelAnimationFrame() {},
    fetch: async () => ({ ok: true, json: async () => ({ channels: 100 }) }),
    queryAll: () => [node],
    queryOne: () => status,
    reducedMotion: () => false,
    requestAnimationFrame: (callback) => {
      frames.push(callback);
      return frames.length;
    },
  });

  await loader();
  frames[0](0);
  frames[1](500);
  assert.equal(node.textContent, '50');
  frames[2](1000);
  assert.equal(node.textContent, '100');
  assert.equal(status.textContent, 'фактические данные рабочего сервиса');
});

test('a reduced-motion change cancels pending metrics and renders the target immediately', async () => {
  const node = stat('channels');
  const frames = [];
  const cancelled = [];
  let onChange;
  const motionQuery = {
    addEventListener(type, listener) {
      assert.equal(type, 'change');
      onChange = listener;
    },
    matches: false,
    removeEventListener() {},
  };
  const loader = createPublicStatsLoader({
    cancelAnimationFrame: (id) => cancelled.push(id),
    fetch: async () => ({ ok: true, json: async () => ({ channels: 240 }) }),
    motionQuery,
    queryAll: () => [node],
    queryOne: () => ({ textContent: '' }),
    reducedMotion: () => motionQuery.matches,
    requestAnimationFrame: (callback) => {
      frames.push(callback);
      return frames.length;
    },
  });

  await loader();
  frames[0](0);
  motionQuery.matches = true;
  onChange({ matches: true });

  assert.equal(node.textContent, '240');
  assert.deepEqual(cancelled, [2]);
  loader.destroy();
});

test('landing reveal uses IntersectionObserver and exact 70ms staggering', () => {
  const delays = [];
  const items = [0, 1, 2].map(() => ({
    classList: new Set(),
    style: { setProperty: (_name, value) => delays.push(value) },
  }));
  const stage = {
    addEventListener() {},
    classList: new Set(),
    querySelector: () => null,
    style: { removeProperty() {}, setProperty() {} },
  };
  const screen = {
    classList: new Set(),
    querySelector: () => stage,
    querySelectorAll: () => items,
  };
  let observerCallback;
  const observed = [];
  class Observer {
    constructor(callback) {
      observerCallback = callback;
    }
    observe(node) {
      observed.push(node);
    }
    unobserve() {}
  }

  const result = initLandingDynamics({
    IntersectionObserver: Observer,
    document: { getElementById: () => screen, querySelectorAll: () => items },
    finePointer: false,
    reducedMotion: () => false,
    requestAnimationFrame: (callback) => {
      callback();
      return 1;
    },
  });

  assert.deepEqual(delays, ['0ms', '70ms', '140ms']);
  assert.deepEqual(observed, items);
  assert.equal(items[0].classList.has('is-inview'), false);
  observerCallback([{ isIntersecting: true, target: items[0] }]);
  assert.equal(items[0].classList.has('is-inview'), true);
  assert.equal(result.parallaxEnabled, false);
});

test('FAQ uses native toggle only, synchronizes ARIA, and animates only panel height', () => {
  const makeDetail = (label, open = false) => {
    const listeners = new Map();
    const attributes = new Map();
    const animations = [];
    const panel = {
      animate: (frames, options) => {
        animations.push({ frames, options });
        return { finished: Promise.resolve() };
      },
      scrollHeight: 84,
      style: { removeProperty() {}, setProperty() {} },
    };
    const summary = { setAttribute: (name, value) => attributes.set(name, value) };
    return {
      addEventListener: (type, listener) => listeners.set(type, listener),
      animations,
      attributes,
      dataset: { faqLabel: label },
      listeners,
      open,
      panel,
      querySelector: (selector) => (selector === 'summary' ? summary : panel),
    };
  };
  const first = makeDetail('Первый', true);
  const second = makeDetail('Второй');
  const nodes = {
    faqCurrent: { textContent: '' },
    faqHint: { textContent: '' },
    faqProgress: { style: {} },
  };
  const document = {
    getElementById: (id) => nodes[id],
    querySelectorAll: () => [first, second],
  };

  initFaq({ document, reducedMotion: () => false });
  assert.deepEqual([...first.listeners.keys()], ['toggle']);
  assert.deepEqual([...second.listeners.keys()], ['toggle']);
  second.open = true;
  second.listeners.get('toggle')();
  assert.equal(first.open, false);
  assert.equal(second.attributes.get('aria-expanded'), 'true');
  assert.equal(nodes.faqCurrent.textContent, '02');
  assert.deepEqual(second.animations[0].frames, [{ height: '0px' }, { height: '84px' }]);
  assert.equal(second.animations[0].options.duration, 420);

  const reduced = makeDetail('Без движения', true);
  initFaq({
    document: {
      getElementById: (id) => nodes[id],
      querySelectorAll: () => [reduced],
    },
    reducedMotion: () => true,
  });
  reduced.listeners.get('toggle')();
  assert.equal(reduced.animations.length, 0);
});

test('dialog opener binding is idempotent and restores the actual clicked opener', () => {
  const dialogListeners = new Map();
  const triggerListeners = new Map();
  const attributes = new Map();
  let prevented = 0;
  let focused = 0;
  const trigger = {
    addEventListener: (type, listener) => triggerListeners.set(type, listener),
    focus: () => {
      focused += 1;
    },
    setAttribute: (name, value) => attributes.set(name, value),
  };
  const dialog = {
    addEventListener: (type, listener) => dialogListeners.set(type, listener),
    close() {
      this.open = false;
      dialogListeners.get('close')?.();
    },
    open: false,
    showModal() {
      this.open = true;
    },
  };

  const first = bindDialogOpeners(dialog, [trigger]);
  const second = bindDialogOpeners(dialog, [trigger]);
  assert.strictEqual(first, second);
  assert.equal(triggerListeners.size, 1);
  assert.equal(attributes.get('aria-haspopup'), 'dialog');
  triggerListeners.get('click')({
    currentTarget: trigger,
    preventDefault: () => {
      prevented += 1;
    },
  });
  dialog.close();
  assert.equal(prevented, 1);
  assert.equal(focused, 1);
});

test('auth and CSS integrations retain safe rendering and shared motion contracts', () => {
  const auth = readFileSync(new URL('../../js/auth.js', import.meta.url), 'utf8');
  const tokens = readFileSync(new URL('../../css/tokens.css', import.meta.url), 'utf8');
  const landing = readFileSync(new URL('../../css/landing-v53.css', import.meta.url), 'utf8');
  const components = readFileSync(new URL('../../css/components.css', import.meta.url), 'utf8');

  assert.match(auth, /bindDialogOpeners/);
  assert.match(auth, /renderText/);
  assert.doesNotMatch(auth, /\.onclick\s*=/);
  assert.doesNotMatch(auth, /\.innerHTML\s*=/);
  for (const token of [
    '--motion-reveal: 420ms',
    '--motion-stagger: 70ms',
    '--motion-dialog: 320ms',
    '--motion-price: 400ms',
    '--motion-metrics: 1000ms',
  ]) {
    assert.match(tokens, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }
  // v53: reveal-контракт живёт в landing-v53.css (класс is-inview ставит
  // js/landing.js), FAQ стал переписко́й на <details class="tl-q">.
  assert.match(landing, /\.rv\.is-inview/);
  assert.match(landing, /\.tl-q summary/);
  assert.match(components, /dialog\[open\][\s\S]*var\(--motion-dialog\)/);
});
