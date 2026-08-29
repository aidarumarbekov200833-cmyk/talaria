import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import { api, clearToken, esc, getToken, setToken } from '../../js/api.js';
import { csvCell, toCsv } from '../../js/shared/csv.js';
import { escapeHtml, renderText, safeUrl } from '../../js/shared/dom.js';
import { attachDialogLifecycle } from '../../js/shared/dialogs.js';
import { MOTION, createNumberTween, prefersReducedMotion } from '../../js/shared/motion.js';
import { get, remove, set } from '../../js/shared/storage.js';

test('storage safely returns fallbacks and reports unavailable writes', () => {
  const broken = {
    getItem() {
      throw new DOMException('denied', 'SecurityError');
    },
    setItem() {
      throw new DOMException('quota', 'QuotaExceededError');
    },
    removeItem() {
      throw new DOMException('denied', 'SecurityError');
    },
  };

  assert.equal(get('mf_token', 'fallback', broken), 'fallback');
  assert.equal(get('mf_token', 'fallback', null), 'fallback');
  assert.equal(get('mf_token', 'fallback', { getItem: () => ({}) }), 'fallback');
  assert.equal(set('mf_token', 'token', broken), false);
  assert.equal(remove('mf_token', broken), false);
});

test('storage delegates successful persistent access to its injected storage', () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };

  assert.equal(set('mf_theme', 'dark', storage), true);
  assert.equal(get('mf_theme', 'light', storage), 'dark');
  assert.equal(remove('mf_theme', storage), true);
  assert.equal(get('mf_theme', 'light', storage), 'light');
});

test('storage treats an explicit null injection as unavailable', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const values = new Map();
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
      removeItem: (key) => values.delete(key),
    },
  });

  try {
    assert.equal(get('mf_theme', 'light', null), 'light');
    assert.equal(set('mf_theme', 'dark', null), false);
    assert.equal(remove('mf_theme', null), false);
    assert.equal(values.size, 0);
  } finally {
    if (original) Object.defineProperty(globalThis, 'localStorage', original);
    else Reflect.deleteProperty(globalThis, 'localStorage');
  }
});

test('api keeps token and esc compatibility facades', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const values = new Map();
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
      removeItem: (key) => values.delete(key),
    },
  });

  try {
    setToken('token');
    assert.equal(getToken(), 'token');
    clearToken();
    assert.equal(getToken(), null);
    assert.equal(esc('<b>unsafe</b>'), '&lt;b&gt;unsafe&lt;/b&gt;');
  } finally {
    if (original) Object.defineProperty(globalThis, 'localStorage', original);
    else Reflect.deleteProperty(globalThis, 'localStorage');
  }
});

test('motion tokens are immutable and reduced-motion detection is environment-safe', () => {
  assert.equal(Object.isFrozen(MOTION), true);
  assert.deepEqual(MOTION, {
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
  assert.equal(prefersReducedMotion(undefined), false);
  assert.equal(prefersReducedMotion({ matchMedia: () => ({ matches: true }) }), true);
});

test('numeric rAF tween cancels the prior frame and reaches its exact final value', () => {
  const queued = [];
  const cancelled = [];
  const values = [];
  const tween = createNumberTween({
    requestAnimationFrame: (callback) => {
      queued.push(callback);
      return queued.length;
    },
    cancelAnimationFrame: (id) => cancelled.push(id),
  });

  tween({ from: 0, to: 10, duration: 100, onUpdate: (value) => values.push(value) });
  tween({ from: 10, to: 20, duration: 100, onUpdate: (value) => values.push(value) });
  assert.deepEqual(cancelled, [1]);
  queued[1](0);
  queued[2](50);
  queued[3](100);
  assert.equal(values.at(-1), 20);

  tween({
    from: 20,
    to: 30,
    duration: 100,
    reducedMotion: true,
    onUpdate: (value) => values.push(value),
  });
  assert.equal(values.at(-1), 30);
});

test('numeric rAF tween ignores stale callbacks and old cancellations cannot stop the latest run', () => {
  const queued = [];
  const cancelled = [];
  const updates = [];
  const tween = createNumberTween({
    requestAnimationFrame: (callback) => {
      queued.push(callback);
      return queued.length;
    },
    cancelAnimationFrame: (id) => cancelled.push(id),
  });

  const cancelFirst = tween({
    from: 0,
    to: 1,
    duration: 100,
    onUpdate: (value) => updates.push(value),
  });
  tween({ from: 1, to: 2, duration: 100, onUpdate: (value) => updates.push(value) });
  cancelFirst();
  queued[0](0);

  assert.deepEqual(cancelled, [1]);
  assert.deepEqual(updates, []);
  queued[1](0);
  queued[2](100);
  assert.equal(updates.at(-1), 2);
});

test('numeric rAF tween emits the exact fractional target and clears its completed frame', () => {
  const queued = [];
  const updates = [];
  const tween = createNumberTween({
    requestAnimationFrame: (callback) => {
      queued.push(callback);
      return queued.length;
    },
    cancelAnimationFrame() {},
  });
  const target = 0.3;

  tween({ from: 0.1, to: target, duration: 100, onUpdate: (value) => updates.push(value) });
  queued[0](0);
  queued[1](100);
  assert.strictEqual(updates.at(-1), target);
  assert.equal(queued.length, 2);
});

test('DOM helpers escape text, render through textContent, and reject unsafe URL schemes', () => {
  assert.equal(escapeHtml('<img src=x onerror=1>'), '&lt;img src=x onerror=1&gt;');
  const element = {};
  renderText(element, '<b>unsafe</b>');
  assert.equal(element.textContent, '<b>unsafe</b>');
  assert.equal(safeUrl('https://example.test/path'), 'https://example.test/path');
  assert.equal(safeUrl('javascript:alert(1)'), null);
  assert.equal(safeUrl('data:text/html,nope'), null);
});

test('CSV quotes special content and neutralizes formula-capable cells', () => {
  assert.equal(csvCell(' a,b"c\n'), '" a,b""c\n"');
  assert.equal(csvCell('\t＝SUM(A1)'), "'\t＝SUM(A1)");
  assert.equal(csvCell('\r\n@cmd'), '"\'\r\n@cmd"');
  assert.equal(csvCell('\u007f=1'), "'\u007f=1");
  assert.equal(csvCell('\u0085＋SUM(A1)'), "'\u0085＋SUM(A1)");
  assert.equal(
    toCsv([
      ['name', '=2+2'],
      ['line', 'a\rb'],
    ]),
    'name,\'=2+2\r\nline,"a\rb"',
  );
});

test('dialog lifecycle remembers the real opener, closes on backdrop, and restores focus', () => {
  let closeListener;
  let clickListener;
  let focused = 0;
  let shown = 0;
  const opener = {
    focus: () => {
      focused += 1;
    },
  };
  const dialog = {
    open: false,
    showModal() {
      shown += 1;
      this.open = true;
    },
    close() {
      this.open = false;
      closeListener();
    },
    addEventListener(type, listener) {
      if (type === 'close') closeListener = listener;
      if (type === 'click') clickListener = listener;
    },
    removeEventListener() {},
  };
  const lifecycle = attachDialogLifecycle(dialog, { activeElement: () => opener });

  lifecycle.open();
  assert.equal(shown, 1);
  clickListener({ target: dialog, currentTarget: dialog });
  assert.equal(focused, 1);
  lifecycle.destroy();
});

test('dialog lifecycle preserves the first opener and detaches its exact listeners', () => {
  const listeners = new Map();
  let firstFocused = 0;
  let secondFocused = 0;
  const first = {
    focus: () => {
      firstFocused += 1;
    },
  };
  const second = {
    focus: () => {
      secondFocused += 1;
    },
  };
  const dialog = {
    open: false,
    showModal() {
      this.open = true;
    },
    close() {
      this.open = false;
      listeners.get('close')?.();
    },
    addEventListener: (type, listener) => listeners.set(type, listener),
    removeEventListener: (type, listener) => {
      assert.strictEqual(listeners.get(type), listener);
      listeners.delete(type);
    },
  };
  let active = first;
  const lifecycle = attachDialogLifecycle(dialog, { activeElement: () => active });

  lifecycle.open();
  active = second;
  lifecycle.open();
  dialog.close();
  assert.equal(firstFocused, 1);
  assert.equal(secondFocused, 0);

  lifecycle.destroy();
  listeners.get('click')?.({ target: dialog, currentTarget: dialog });
  listeners.get('close')?.();
  assert.equal(firstFocused, 1);
});

test('repeated native dialog lifecycle setup reuses listeners and restores focus once', () => {
  const listeners = [];
  let focusCount = 0;
  const opener = {
    focus: () => {
      focusCount += 1;
    },
  };
  const dialog = {
    addEventListener(type, listener) {
      listeners.push({ listener, type });
    },
    close() {
      this.open = false;
      listeners.find(({ type }) => type === 'close')?.listener();
    },
    open: false,
    showModal() {
      this.open = true;
    },
  };

  const first = attachDialogLifecycle(dialog, { activeElement: () => opener });
  const second = attachDialogLifecycle(dialog, { activeElement: () => opener });
  first.open();
  dialog.close();

  assert.strictEqual(first, second);
  assert.equal(listeners.length, 2);
  assert.equal(focusCount, 1);
});

test('synchronous theme boot keeps the approved dark default when localStorage is inaccessible', () => {
  const attributes = new Map();
  const context = {
    document: {
      documentElement: {
        style: { setProperty() {} },
        setAttribute: (key, value) => attributes.set(key, value),
      },
    },
  };
  const boot = readFileSync(new URL('../../js/theme-boot.js', import.meta.url), 'utf8');

  assert.doesNotThrow(() => {
    vm.runInNewContext(
      "Object.defineProperty(globalThis, 'localStorage', { get() { throw new Error('denied'); } });\n" +
        boot,
      context,
    );
  });
  assert.equal(attributes.get('data-theme'), 'dark');
});

test('api forwards an optional abort signal without changing its three-argument request shape', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  const controller = new AbortController();
  globalThis.fetch = async (path, options) => {
    requests.push({ options, path });
    return { json: async () => ({ ok: true }), ok: true, status: 200 };
  };

  try {
    await api('POST', '/api/plans/quote', { currency: 'RUB' }, { signal: controller.signal });
    await api('GET', '/api/plans');
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.strictEqual(requests[0].options.signal, controller.signal);
  assert.equal(requests[0].options.body, JSON.stringify({ currency: 'RUB' }));
  assert.equal('signal' in requests[1].options, false);
});
