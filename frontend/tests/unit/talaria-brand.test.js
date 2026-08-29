import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8');
const talariaCss = readFileSync(new URL('../../css/talaria-v5.css', import.meta.url), 'utf8');

test('public product identity is Talaria while compatibility identifiers stay internal', () => {
  assert.match(html, /<title>Talaria\b/);
  assert.match(html, /class="landing-brand-name">Talaria</);
  assert.doesNotMatch(html, /Zephyr|ZEPHYR/);
  assert.doesNotMatch(talariaCss, /Talaria-derived Zephyr/i);
});

test('Talaria finale keeps the seven-letter word, hand, letter and login action', () => {
  const finale = html.match(/<section[^>]+id="zephyrFinale"[\s\S]*?<\/section>/)?.[0];

  assert.ok(finale, 'finale section must remain in the contract DOM');
  assert.match(finale, /TALARIA/);
  assert.equal((finale.match(/class="zp-final-letter-char"/g) || []).length, 7);
  assert.match(finale, /id="zpFinalHand"/);
  assert.match(finale, /class="zp-final-letter[^\"]*"/);
  assert.match(finale, />Войти\s*→?</);
});

test('landing remains scrollable and the Talaria finale owns a long sticky scroll stage', () => {
  assert.match(
    talariaCss,
    /body:has\(\.login-screen:not\(\.hidden\)\)[^{]*\{[^}]*overflow-y:\s*auto/s,
  );
  assert.match(talariaCss, /\.zp-final\s*\{[^}]*height:\s*320vh/s);
  assert.match(talariaCss, /\.zp-final-sticky\s*\{[^}]*position:\s*sticky/s);
  assert.match(talariaCss, /\.zp-final-sticky\s*\{[^}]*width:\s*100%/s);
});

test('landing theme toggle is a round icon button showing one icon per theme', () => {
  // Переделан из пилюли в круглую иконку-кнопку: в тёмной теме солнце,
  // в светлой — луна (показываем, куда переключит клик).
  assert.match(talariaCss, /\.landing-theme-toggle\s*\{[^}]*border-radius:\s*50%/s);
  assert.match(
    talariaCss,
    /html\[data-theme='dark'\]\s+#loginScreen\s+\.landing-theme-moon\s*\{[^}]*display:\s*none/s,
  );
  assert.match(
    talariaCss,
    /html\[data-theme='light'\]\s+#loginScreen\s+\.landing-theme-sun\s*\{[^}]*display:\s*none/s,
  );
});
