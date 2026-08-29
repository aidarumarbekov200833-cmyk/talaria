import assert from 'node:assert/strict';
import test from 'node:test';

import { createPublicStatsLoader } from '../../js/landing.js';

test('each public metric owns an independent 1000ms tween', async () => {
  const nodes = [
    { dataset: { publicStat: 'channels' }, textContent: '' },
    { dataset: { publicStat: 'leads' }, textContent: '' },
  ];
  const frames = [];
  const loader = createPublicStatsLoader({
    cancelAnimationFrame() {},
    fetch: async () => ({
      json: async () => ({ channels: 10, leads: 20 }),
      ok: true,
    }),
    queryAll: () => nodes,
    queryOne: () => ({ textContent: '' }),
    reducedMotion: () => false,
    requestAnimationFrame: (callback) => {
      frames.push(callback);
      return frames.length;
    },
  });

  await loader();
  assert.equal(frames.length, 2);
  frames[0](0);
  frames[1](0);
  frames[2](1000);
  frames[3](1000);
  assert.deepEqual(
    nodes.map((node) => node.textContent),
    ['10', '20'],
  );
});
