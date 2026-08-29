/* Synchronous pre-paint appearance: no flash, no per-session font/accent drift. */
(() => {
  let savedTheme = null;
  try {
    savedTheme = localStorage.getItem('mf_theme');
  } catch (_) {}
  const theme = savedTheme === 'light' ? 'light' : 'dark';
  const root = document.documentElement;
  root.setAttribute('data-theme', theme);
  root.setAttribute('data-font', 'brand');
})();
