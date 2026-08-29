const SAFE_PROTOCOLS = new Set(['http:', 'https:', 'mailto:', 'tel:']);

export function escapeHtml(value) {
  return String(value ?? '').replace(
    /[&<>"']/g,
    (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char],
  );
}

export function renderText(element, value) {
  element.textContent = String(value ?? '');
  return element;
}

export function safeUrl(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return null;
  if (raw.startsWith('#') || raw.startsWith('/') || raw.startsWith('./') || raw.startsWith('../'))
    return raw;
  try {
    const parsed = new URL(raw);
    return SAFE_PROTOCOLS.has(parsed.protocol) ? raw : null;
  } catch (_) {
    return null;
  }
}
