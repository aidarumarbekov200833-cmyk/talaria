import assert from 'node:assert/strict';
import test from 'node:test';

import { createPublicStatsLoader } from '../../js/landing.js';

test('null, boolean, and empty-string metrics fail closed instead of coercing to zero', async () => {
  const nodes = ['leads', 'databases', 'messages_sent'].map((publicStat) => ({
    dataset: { publicStat },
    textContent: '',
  }));
  const status = { textContent: '' };
  const loader = createPublicStatsLoader({
    fetch: async () => ({
      json: async () => ({ databases: false, leads: null, messages_sent: '' }),
      ok: true,
    }),
    queryAll: () => nodes,
    queryOne: () => status,
  });

  await loader();

  assert.deepEqual(
    nodes.map((node) => node.textContent),
    ['—', '—', '—'],
  );
  assert.equal(status.textContent, 'Данные временно недоступны');
});
