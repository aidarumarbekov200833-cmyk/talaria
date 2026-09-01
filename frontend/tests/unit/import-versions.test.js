import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

// Версии модулей (?v=...) обязаны совпадать во всех местах импорта: браузер
// считает `chat.js?v=1` и `chat.js?v=2` РАЗНЫМИ модулями, каждый со своим
// состоянием. Ручной бамп в шести файлах гарантированно когда-нибудь
// разъедется, поэтому расхождение ловит тест, а не пользователь с двумя
// экземплярами chat.js.

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (name) => readFileSync(resolve(frontend, name), 'utf8');

const sources = [
  'index.html',
  ...readdirSync(resolve(frontend, 'js'))
    .filter((f) => f.endsWith('.js'))
    .map((f) => `js/${f}`),
];

/** Собрать { module: Map<version|'', Set<file>> } по всем импортам с ?v=. */
function collectVersions() {
  const seen = new Map();
  const pattern = /([\w-]+\.js)\?v=([\w.-]+)/g;
  for (const file of sources) {
    const text = read(file);
    for (const match of text.matchAll(pattern)) {
      const [, module, version] = match;
      if (!seen.has(module)) seen.set(module, new Map());
      const versions = seen.get(module);
      if (!versions.has(version)) versions.set(version, new Set());
      versions.get(version).add(file);
    }
  }
  return seen;
}

test('каждый версионируемый модуль импортируется с одной и той же версией', () => {
  const seen = collectVersions();
  assert.ok(seen.size > 0, 'импорты с ?v= не найдены — регэксп сломан?');
  const problems = [];
  for (const [module, versions] of seen) {
    if (versions.size > 1) {
      const detail = [...versions.entries()]
        .map(([version, files]) => `v=${version} в ${[...files].join(', ')}`)
        .join(' | ');
      problems.push(`${module}: ${detail}`);
    }
  }
  assert.deepEqual(problems, [], `Разъехались версии модулей:\n${problems.join('\n')}`);
});

test('версионируемый модуль нигде не импортируется без ?v=', () => {
  const seen = collectVersions();
  const problems = [];
  for (const [module] of seen) {
    for (const file of sources) {
      const text = read(file);
      // Импорт вида ./module.js без ?v= — второй экземпляр модуля в браузере.
      const bare = new RegExp(`\\./${module.replace('.', '\\.')}(?!\\?v=)['"\\)]`);
      if (bare.test(text)) problems.push(`${module} без версии в ${file}`);
    }
  }
  assert.deepEqual(problems, [], problems.join('\n'));
});
