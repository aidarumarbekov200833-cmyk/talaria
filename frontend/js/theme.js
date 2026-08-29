/** Talaria appearance: two themes, one product typeface, one brand accent. */
import { get, remove, set } from './shared/storage.js';

export const THEME_KEY = 'mf_theme';
export const FONT_KEY = 'outly_font';
export const ACCENT_COLOR_KEY = 'outly_accent_color';

export const THEMES = [
  { id: 'light', name: 'Светлая', sub: 'Чистая и спокойная' },
  { id: 'dark', name: 'Тёмная', sub: 'Комфортная вечером' },
];

const root = document.documentElement;
let ready = false;

function normalizeTheme(value) {
  return value === 'dark' ? 'dark' : 'light';
}

export function currentTheme() {
  return normalizeTheme(root.getAttribute('data-theme'));
}

export function isDark() {
  return currentTheme() === 'dark';
}

function paint() {
  document.querySelectorAll('#themeGrid .theme-card').forEach((card) => {
    const active = card.dataset.theme === currentTheme();
    card.classList.toggle('active', active);
    card.setAttribute('aria-pressed', String(active));
  });
  document.querySelectorAll('#landingThemeToggle').forEach((button) => {
    const dark = isDark();
    button.classList.toggle('is-dark', dark);
    button.setAttribute('aria-pressed', String(dark));
    button.setAttribute('aria-label', dark ? 'Включить светлую тему' : 'Включить тёмную тему');
    button.dataset.tip = dark ? 'Светлая тема' : 'Тёмная тема';
  });
}

export function setTheme(value) {
  const theme = normalizeTheme(value);
  root.setAttribute('data-theme', theme);
  root.setAttribute('data-font', 'brand');
  set(THEME_KEY, theme);
  remove(FONT_KEY);
  remove(ACCENT_COLOR_KEY);
  paint();
  const detail = { theme };
  document.dispatchEvent(new CustomEvent('mf:themechange', { detail, bubbles: true }));
  window.dispatchEvent(new CustomEvent('mf:themechange', { detail }));
}

// Совместимость для старых модулей/сохранённых AI-действий: выборов больше нет.
export function setFont() {
  root.setAttribute('data-font', 'brand');
  return 'brand';
}
export function currentFont() {
  return 'brand';
}
export function setAccentColor() {
  return '#5B7CFF';
}

function renderPicker() {
  const box = document.getElementById('themeGrid');
  if (!box) return;
  box.innerHTML = THEMES.map(
    (theme) => `
    <button type="button" class="theme-card" data-theme="${theme.id}" aria-pressed="false">
      <span class="theme-preview ${theme.id}" aria-hidden="true"><i></i><b></b><em></em></span>
      <span class="theme-meta"><span class="theme-name">${theme.name}</span><span class="theme-sub">${theme.sub}</span></span>
      <span class="theme-check" aria-hidden="true">✓</span>
    </button>`,
  ).join('');
  box.addEventListener('click', (event) => {
    const card = event.target.closest('.theme-card');
    if (card) setTheme(card.dataset.theme);
  });
}

export function initTheme() {
  root.setAttribute('data-theme', normalizeTheme(get(THEME_KEY)));
  root.setAttribute('data-font', 'brand');
  if (!ready) {
    ready = true;
    renderPicker();
    document.querySelectorAll('#landingThemeToggle').forEach((button) => {
      button.addEventListener('click', () => setTheme(isDark() ? 'light' : 'dark'));
    });
  }
  paint();
}
