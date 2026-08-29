/** Safe access to optional browser storage. */
function browserStorage() {
  try {
    return globalThis.localStorage;
  } catch (_) {
    return null;
  }
}

function resolveStorage(storage) {
  return storage === undefined ? browserStorage() : storage;
}

export function get(key, fallback = null, storage) {
  try {
    const value = resolveStorage(storage)?.getItem(key);
    return typeof value === 'string' ? value : fallback;
  } catch (_) {
    return fallback;
  }
}

export function set(key, value, storage) {
  try {
    const target = resolveStorage(storage);
    if (!target) return false;
    target.setItem(key, value);
    return true;
  } catch (_) {
    return false;
  }
}

export function remove(key, storage) {
  try {
    const target = resolveStorage(storage);
    if (!target) return false;
    target.removeItem(key);
    return true;
  } catch (_) {
    return false;
  }
}
