import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const css = (name) => readFileSync(resolve(frontend, 'css', name), 'utf8');

test('styles entrypoint keeps the base cascade and applies the approved theme last', () => {
  assert.equal(
    css('styles.css').trim(),
    [
      // v53: шрифты self-hosted (fonts.css) первыми, лендинг v53 — последним,
      // чтобы его правила побеждали остатки кабинетного каскада.
      "@import url('./fonts.css');",
      "@import url('./tokens.css');",
      "@import url('./base.css');",
      "@import url('./components.css');",
      "@import url('./landing.css');",
      "@import url('./app.css');",
      "@import url('./talaria.css');",
      "@import url('./talaria-v5.css');",
      "@import url('./cabinet.css');",
      "@import url('./landing-v53.css');",
    ].join('\n'),
  );
});

test('only tokens define CSS colour literals and motion uses shared tokens', () => {
  const names = [
    'base.css',
    'components.css',
    'landing.css',
    'app.css',
    'talaria.css',
    'talaria-v5.css',
    'cabinet.css',
    'landing-v53.css',
  ];
  const forbidden = /#[0-9a-f]{3,8}\b|\b(?:rgb|hsl)a?\(/i;
  for (const name of names) assert.doesNotMatch(css(name), forbidden, name);
  const joined = names.map(css).join('\n');
  // v46: Unbounded снят с запрета — владелец вернул его как дисплейный шрифт
  // (редизайн «как Viktor», 2026-09-01). Запрет был стражем серифной эпохи v34+.
  assert.doesNotMatch(joined, /OUTLY|Manrope|--theme-/i);
  assert.doesNotMatch(joined, /transition:[^;}]*\b(?:\d+ms|\.\d+s)/i);
});

test('approved messenger tokens and accessibility motion rules are present', () => {
  const tokens = css('tokens.css');
  for (const value of [
    '#FAF5FB',
    '#FFFFFF',
    '#171019',
    '#FF2FA4',
    '26px',
    '20px',
    '12px',
    '1440px',
    '250ms',
    '360ms',
    '480ms',
  ]) {
    assert.match(tokens, new RegExp(value.replace(/[.#]/g, '\\$&'), 'i'));
  }
  assert.match(tokens, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(tokens, /--grid-columns:\s*12/);
});

test('every captured class token remains in the document', () => {
  const contract = JSON.parse(
    readFileSync(resolve(frontend, 'tests/contracts/dom-contract.json'), 'utf8'),
  );
  const html = readFileSync(resolve(frontend, 'index.html'), 'utf8');
  const classes = new Set(contract.flatMap((record) => record.classes));
  for (const className of classes) {
    assert.match(
      html,
      new RegExp(`(?:class=["'][^"']*\\b${className}\\b|class=["'][^"']*${className}(?:\\s|["']))`),
    );
  }
});

test('synchronous theme boot does not override the token colour system', () => {
  const boot = readFileSync(resolve(frontend, 'js/theme-boot.js'), 'utf8');
  assert.doesNotMatch(boot, /style\.setProperty\(['"]--(?:accent|grad|ring)/);
  assert.match(boot, /localStorage\.getItem\(['"]mf_theme['"]\)/);
  assert.match(boot, /setAttribute\(['"]data-font['"], ['"]brand['"]\)/);
});

test('static markup does not disclose the private promo code or a discount amount', () => {
  const html = readFileSync(resolve(frontend, 'index.html'), 'utf8');
  assert.doesNotMatch(html, /(?:ZEPHYR|TALARIA)30|(?:−|-)30%/i);
});

test('workspace shell and mobile navigation retain deliberate visual coverage', () => {
  const app = `${css('app.css')}\n${css('components.css')}`;
  for (const selector of [
    '.app-wrapper',
    '.bottom-nav',
    '.global-actions',
    '.brand-mark',
    '.nav-group',
    // v40: квота и плитка тарифа переехали в меню аккаунта — теперь покрытие
    // проверяем на нём, а не на удалённых блоках сайдбара.
    '.acc-menu',
    '.acc-links',
    '.acc-limit-bar',
    '.page-sub',
    '.sidebar-bottom',
    '.drawer-input',
    '.drawer.show',
    '.onb-overlay.show',
    '.modal-overlay.show',
  ]) {
    assert.match(app, new RegExp(selector.replace('.', '\\.')));
  }
});

test('problematic workspace component families have bounded visual primitives', () => {
  const app = css('app.css');
  for (const selector of [
    '.offer-upload',
    '.offer-upload-icon',
    '.tariff-single-grid',
    '.tariff-card-single',
    '.health-management-icon',
    '.campaign-section-label',
    '.dashboard-clock',
    '.offer-kind-card',
    '.reply-heatmap',
    '.health-trend-copy',
  ]) {
    assert.match(app, new RegExp(selector.replace('.', '\\.')));
  }
  assert.match(app, /\.offer-upload-icon svg\s*\{[^}]*width:\s*24px/);
  assert.match(app, /\.health-management-icon svg\s*\{[^}]*width:\s*22px/);
  assert.match(app, /\.tariff-single-grid\s*\{[^}]*grid-template-columns:\s*1fr/);
});

test('runtime tariff markup has a cohesive responsive CSS contract', () => {
  const app = css('app.css');
  const components = css('components.css');
  for (const selector of [
    '.promo-ticket',
    '.tariff-topline',
    '.tariff-price-live',
    '.plan-period',
    '.plan-currency',
    '.tariff-feats',
  ]) {
    assert.match(
      selector === '.promo-ticket' ? components : app,
      new RegExp(selector.replace('.', '\\.')),
    );
  }
  assert.match(app, /\.tariff-feats svg\s*\{[^}]*width:\s*(?:20|22|24)px/);
  assert.match(app, /\.tariff-feats svg\s*\{[^}]*height:\s*(?:20|22|24)px/);
  assert.match(app, /@media \(max-width: 700px\)[\s\S]*\.tariff-topline/);
});

test('landing sections retain the required sentinel order through the finale', () => {
  const html = readFileSync(resolve(frontend, 'index.html'), 'utf8');
  // Подвал-«текстовый» блок убран по просьбе владельца — сцена-финал теперь
  // и есть визуальный конец страницы.
  // v20: порядок пересобран по дизайн-аудиту — сначала продукт («так выглядит
  // доставка», возможности, полёт кампании), и только потом фактические цифры
  // сервиса. Раньше «Динамика» стояла вторым экраном, до объяснения продукта.
  // v53 (2026-09-02): лендинг собран с нуля — одна сюжетная линия письма:
  // герой с переписко́й → живые цифры → четыре шага → безопасность → тариф →
  // вопросы → финал с подвалом.
  const sentinels = [
    '<header class="tl-nav"',
    '<section class="tl-hero"',
    '<section class="tl-pulse"',
    '<section class="tl-how"',
    '<section class="tl-safe"',
    '<section class="tl-price"',
    '<section class="tl-faq"',
    '<section class="tl-final"',
  ];
  let previous = -1;
  for (const sentinel of sentinels) {
    const index = html.indexOf(sentinel);
    assert.ok(index > previous, `expected ${sentinel} after preceding landing sentinel`);
    previous = index;
  }
  assert.doesNotMatch(html, /class="lp-foot zd-footer"/, 'text footer must stay removed');
});
