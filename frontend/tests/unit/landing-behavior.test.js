import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createPublicStatsLoader,
  initLandingDynamics,
  renderFinaleProgress,
} from '../../js/landing.js';
import { createFaqController } from '../../js/faq.js';

function stat() {
  return { dataset: {}, textContent: '' };
}

test('public metrics use a 1000ms tween with an intermediate value and exact final value', async () => {
  const nodes = [stat()];
  nodes[0].dataset.publicStat = 'channels';
  const calls = [];
  const loader = createPublicStatsLoader({
    fetch: async () => ({ ok: true, json: async () => ({ channels: 100 }) }),
    queryAll: () => nodes,
    queryOne: () => ({ textContent: '' }),
    tween: (options) => {
      calls.push(options);
      options.onUpdate(50);
      options.onUpdate(options.to);
    },
  });

  await loader();

  assert.equal(calls[0].duration, 1000);
  assert.equal(nodes[0].textContent, '100');
});

test('public metrics render their final value immediately with reduced motion', async () => {
  const nodes = [stat()];
  nodes[0].dataset.publicStat = 'channels';
  let tweened = false;
  const loader = createPublicStatsLoader({
    fetch: async () => ({ ok: true, json: async () => ({ channels: 100 }) }),
    queryAll: () => nodes,
    queryOne: () => ({ textContent: '' }),
    reducedMotion: () => true,
    tween: () => {
      tweened = true;
    },
  });

  await loader();

  assert.equal(tweened, false);
  assert.equal(nodes[0].textContent, '100');
});

test('public metric failures and invalid server values retain em dashes with exact feedback', async () => {
  const nodes = [stat(), stat()];
  nodes[0].dataset.publicStat = 'channels';
  nodes[1].dataset.publicStat = 'leads';
  const status = { textContent: '' };
  const loader = createPublicStatsLoader({
    fetch: async () => ({ ok: true, json: async () => ({ channels: Infinity, leads: '<img>' }) }),
    queryAll: () => nodes,
    queryOne: () => status,
  });

  await loader();

  assert.deepEqual(
    nodes.map((node) => node.textContent),
    ['—', '—'],
  );
  assert.equal(status.textContent, 'Данные временно недоступны');
});

test('landing dynamics use the approved reveal timing and disable reveal/parallax under reduced motion', () => {
  const items = [{ classList: new Set(), style: { setProperty() {} } }];
  const phone = { style: { removeProperty() {} } };
  const stage = {
    classList: new Set(),
    querySelector: () => phone,
    addEventListener() {},
  };
  const screen = {
    classList: new Set(),
    querySelector: () => stage,
    querySelectorAll: () => items,
  };
  const result = initLandingDynamics({
    document: { getElementById: () => screen, querySelectorAll: () => items },
    reducedMotion: () => true,
    requestAnimationFrame: () => {
      throw new Error('must not animate reduced motion');
    },
  });

  assert.equal(result.duration, 420);
  assert.equal(result.stagger, 70);
  assert.equal(result.parallaxEnabled, false);
  assert.equal(items[0].classList.has('is-inview'), true);
});

test('FAQ keeps one native details item open and synchronizes aria progress feedback', () => {
  const one = { dataset: { faqLabel: 'Первый' }, open: false, summary: { setAttribute() {} } };
  const two = { dataset: { faqLabel: 'Второй' }, open: false, summary: { setAttribute() {} } };
  const current = { textContent: '' };
  const hint = { textContent: '' };
  const progress = { style: {} };
  const faq = createFaqController([one, two], { current, hint, progress });

  faq.sync(two);

  assert.equal(one.open, false);
  assert.equal(two.open, true);
  assert.equal(current.textContent, '02');
  assert.equal(hint.textContent, 'Второй');
  assert.equal(progress.style.width, '100%');
});

test('finale sends the word down before revealing the hand, letter and login action', () => {
  const elements = {
    ghost: { style: {} },
    hand: { style: {} },
    letter: { style: {} },
    login: { style: {} },
    wordClip: { style: {} },
    letters: Array.from({ length: 6 }, () => ({ style: {} })),
  };

  renderFinaleProgress(elements, 0.9, false);

  assert.match(elements.wordClip.style.transform, /translate3d\(0, [1-9][0-9.]*vh, 0\)/);
  assert.ok(Number(elements.hand.style.opacity) > 0.9);
  assert.match(elements.hand.style.transform, /translate3d\(-46%,/);
  assert.ok(Number(elements.letter.style.opacity) > 0.9);
  assert.equal(elements.login.style.opacity, '1');
});

test('reduced motion exposes the complete finale without a scroll animation', () => {
  const elements = {
    ghost: { style: {} },
    hand: { style: {} },
    letter: { style: {} },
    login: { style: {} },
    wordClip: { style: {} },
    letters: Array.from({ length: 6 }, () => ({ style: {} })),
  };

  renderFinaleProgress(elements, 0, true);

  assert.equal(elements.hand.style.opacity, '1');
  assert.equal(elements.letter.style.opacity, '1');
  assert.equal(elements.login.style.opacity, '1');
  assert.equal(elements.wordClip.style.opacity, '0');
});
