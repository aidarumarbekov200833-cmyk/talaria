import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

// Гигиена сессии и приватности (ревью 2026-09-01). Тесты источникового уровня,
// как и остальные unit-тесты фронта: проверяем, что защитные конструкции не
// исчезнут при рефакторинге.

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (name) => readFileSync(resolve(frontend, name), 'utf8');

test('clearToken снимает и админский резервный токен режима просмотра', () => {
  const api = read('js/api.js');
  const clearToken = api.slice(api.indexOf('export function clearToken'));
  assert.match(clearToken, /remove\('mf_token'\)/);
  assert.match(clearToken, /remove\('mf_token_admin'\)/);
});

test('tryAutoLogin разлогинивает только по 401/403, а не по любому сбою', () => {
  const auth = read('js/auth.js');
  const fn = auth.slice(auth.indexOf('async function tryAutoLogin'));
  // Токен снимается только внутри ветки с проверкой статуса.
  assert.match(fn, /err\.status === 401 \|\| err\.status === 403/);
});

test('logout чистит персональные ключи localStorage и глушит опросы', () => {
  const auth = read('js/auth.js');
  assert.match(auth, /talaria\.search-filters\.v1/);
  assert.match(auth, /talaria_notif_seen/);
  assert.match(auth, /new CustomEvent\('talaria:logout'\)/);
  for (const module of ['js/chat.js', 'js/campaigns.js', 'js/search.js', 'js/support.js']) {
    assert.match(read(module), /talaria:logout/, `${module} должен слушать talaria:logout`);
  }
});

test('история чата защищена от гонки диалогов', () => {
  const chat = read('js/chat.js');
  assert.match(chat, /requestedPeer/);
});

test('nginx: лимит тела для /api/ и limit_req для приёма аналитики', () => {
  const nginx = read('nginx.conf');
  assert.match(nginx, /client_max_body_size 12m/);
  assert.match(nginx, /limit_req_zone[^\n]*umami_send/);
  assert.match(nginx, /limit_req zone=umami_send/);
});

test('413 объясняется человеку, а не отдаётся голым кодом', () => {
  assert.match(read('js/api.js'), /413/);
});

test('публичный контур указывает на прод-домен, а не на старый azure-хост', () => {
  for (const name of ['index.html', 'robots.txt', 'sitemap.xml']) {
    const content = read(name);
    assert.doesNotMatch(content, /zapp\.westus2\.cloudapp\.azure\.com/, name);
  }
  assert.match(read('index.html'), /https:\/\/talaria\.mooo\.com\/insights/);
});
