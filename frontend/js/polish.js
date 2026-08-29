/** Compatibility facade for legacy bootstrap callers. */
import { initLanding } from './landing.js';
import { initFaq } from './faq.js';

function bindExpander(buttonId, target, expandedClass = 'is-expanded') {
  const button = document.getElementById(buttonId);
  const box = typeof target === 'string' ? document.querySelector(target) : target;
  if (!button || !box) return;
  button.addEventListener('click', () => {
    const expanded = box.classList.toggle(expandedClass);
    button.textContent = expanded ? 'Свернуть' : 'Показать все';
    button.setAttribute('aria-expanded', String(expanded));
  });
  button.setAttribute('aria-expanded', 'false');
}

function initLoadingSkeletons() {
  const sync = (root) => {
    const nodes = root.matches?.('.dd-empty') ? [root] : root.querySelectorAll?.('.dd-empty') || [];
    nodes.forEach((node) =>
      node.classList.toggle('loading-state', /^Загрузка/.test(node.textContent.trim())),
    );
  };
  sync(document.body);
  const observer = new MutationObserver((records) => {
    records.forEach((record) => {
      if (record.target.nodeType === Node.ELEMENT_NODE) sync(record.target);
      record.addedNodes.forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE) sync(node);
      });
    });
  });
  observer.observe(document.body, { childList: true, subtree: true, characterData: true });
}

function initActionMenus() {
  document.addEventListener('click', (event) => {
    document.querySelectorAll('.action-menu[open]').forEach((menu) => {
      if (!menu.contains(event.target) || event.target.closest('button'))
        menu.removeAttribute('open');
    });
  });
}

let ready = false;

export function initPolish() {
  if (ready) return;
  ready = true;
  bindExpander('activityShowAll', '#dashboard .activity-panel');
  bindExpander('adminRecentToggle', '#admin .admin-activity-panel');
  bindExpander('adminActivityToggle', '#admin .admin-journal-panel');
  initLoadingSkeletons();
  initLanding();
  initFaq();
  initActionMenus();
}
