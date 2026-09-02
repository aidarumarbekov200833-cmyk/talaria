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

test('Talaria finale keeps one signup action and the footer contacts', () => {
  const finale = html.match(/<section class="tl-final"[\s\S]*?<\/section>/)?.[0];
  assert.ok(finale, 'finale section must remain in the contract DOM');
  assert.match(finale, /data-telegram-login-trigger/);
  assert.match(finale, />Начать бесплатно</);
  assert.match(finale, /privacy\.html/);
  assert.match(finale, /terms\.html/);
  // v47/v53: гравюры и растровые картинки не возвращаются.
  assert.doesNotMatch(finale, /<img|fin-palm|fin-letter-float/);
});

test('landing remains scrollable and every CTA on the page has one signup intent', () => {
  const landing = readFileSync(new URL('../../css/landing-v53.css', import.meta.url), 'utf8');
  assert.match(
    landing,
    /body:has\(\.login-screen\.tl:not\(\.hidden\)\)[^{]*\{[^}]*overflow-y:\s*auto/s,
  );
  const screen = html.match(
    /<div class="login-screen tl" id="loginScreen">[\s\S]*?<main class="tl-main">[\s\S]*?<\/main>/,
  )?.[0];
  assert.ok(screen);
  const ctas = screen.match(/data-telegram-login-trigger[\s\S]*?>([^<]+)</g) || [];
  // Одна и та же формулировка для одного намерения: «Войти» в шапке, «Начать бесплатно» в теле.
  const labels = new Set(
    ctas.map((c) =>
      c
        .replace(/^[\s\S]*>/, '')
        .replace(/<$/, '')
        .trim(),
    ),
  );
  assert.deepEqual([...labels].sort(), ['Войти', 'Начать бесплатно']);
});

test('landing theme toggle is a round icon button showing one icon per theme', () => {
  const landing = readFileSync(new URL('../../css/landing-v53.css', import.meta.url), 'utf8');
  assert.match(landing, /\.tl-icon\s*\{[^}]*border-radius:\s*50%/s);
  assert.match(
    landing,
    /html\[data-theme='dark'\]\s+#loginScreen\s+\.landing-theme-moon[^{]*\{[^}]*display:\s*none/s,
  );
  assert.match(
    landing,
    /html\[data-theme='light'\]\s+#loginScreen\s+\.landing-theme-sun\s*\{[^}]*display:\s*none/s,
  );
});
