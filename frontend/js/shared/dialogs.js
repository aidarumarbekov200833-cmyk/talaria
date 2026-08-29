/** Attach native-dialog lifecycle behavior without replacing native focus semantics. */
const lifecycles = new WeakMap();
const openerBindings = new WeakMap();

export function attachDialogLifecycle(
  dialog,
  { activeElement = () => document.activeElement } = {},
) {
  if (!dialog) return null;
  const existing = lifecycles.get(dialog);
  if (existing) return existing;

  let opener = null;
  let destroyed = false;

  const restoreFocus = () => {
    const target = opener;
    opener = null;
    target?.focus?.();
  };
  const closeOnBackdrop = (event) => {
    if (event.target === dialog && event.currentTarget === dialog) dialog.close();
  };

  dialog.addEventListener('close', restoreFocus);
  dialog.addEventListener('click', closeOnBackdrop);

  const lifecycle = {
    open(explicitOpener) {
      if (destroyed || dialog.open) return;
      opener = explicitOpener ?? activeElement();
      dialog.showModal();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      opener = null;
      dialog.removeEventListener?.('close', restoreFocus);
      dialog.removeEventListener?.('click', closeOnBackdrop);
      if (lifecycles.get(dialog) === lifecycle) lifecycles.delete(dialog);
    },
  };
  lifecycles.set(dialog, lifecycle);
  return lifecycle;
}

/** Bind one native dialog to any number of openers without duplicate listeners. */
export function bindDialogOpeners(dialog, openers, { activeElement } = {}) {
  if (!dialog) return null;
  let state = openerBindings.get(dialog);
  if (!state) {
    const lifecycle = attachDialogLifecycle(dialog, activeElement ? { activeElement } : undefined);
    const listeners = new Map();
    const binding = {
      lifecycle,
      destroy() {
        listeners.forEach((listener, opener) => {
          opener.removeEventListener?.('click', listener);
        });
        listeners.clear();
        if (openerBindings.get(dialog)?.binding === binding) openerBindings.delete(dialog);
      },
    };
    state = { binding, lifecycle, listeners };
    openerBindings.set(dialog, state);
  }

  for (const opener of openers || []) {
    if (!opener || state.listeners.has(opener)) continue;
    opener.setAttribute?.('aria-haspopup', 'dialog');
    const listener = (event) => {
      event.preventDefault?.();
      state.lifecycle.open(event.currentTarget ?? opener);
    };
    opener.addEventListener('click', listener);
    state.listeners.set(opener, listener);
  }

  return state.binding;
}
