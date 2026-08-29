/** Онбординг: короткий тур после первого входа + «кубики» быстрого старта
 *  прямо на Главной.
 *
 *  Смысл не в «красивом приветствии»: порядок работы в Talaria неочевиден.
 *  Без ключа YouTube API поиск не запустится, без базы нечего рассылать,
 *  без прогретого Telegram-аккаунта рассылка кончится блокировкой. Новый
 *  человек должен увидеть эту цепочку до того, как начнёт тыкать кнопки.
 *
 *  Тур можно пропустить, но не одним случайным кликом: первый клик по
 *  «Пропустить» показывает короткое подтверждение (Samat), потому что без
 *  ключа API продукт у человека просто не заработает и он уйдёт.
 *
 *  Флаги лежат в localStorage, а не на сервере: это косметика интерфейса,
 *  ради неё не стоит заводить колонку в базе и миграцию. Цена ошибки —
 *  человек увидит подсказку ещё раз на другом компьютере.
 */
import { goTo } from './nav.js?v=zephyr4';

const SEEN_KEY = 'mf_onboarded';
const QS_HIDDEN_KEY = 'mf_quickstart_hidden';
const TOTAL_STEPS = 4;

let step = 1;
let skipArmed = false;

function el(id) {
  return document.getElementById(id);
}

/** Перерисовать видимый шаг, точки прогресса и подписи кнопок. */
function render() {
  document.querySelectorAll('#onbModal [data-onb-step]').forEach((pane) => {
    pane.hidden = Number(pane.dataset.onbStep) !== step;
  });
  document.querySelectorAll('#onbDots .onb-dot').forEach((dot, i) => {
    dot.classList.toggle('active', i + 1 === step);
    dot.classList.toggle('done', i + 1 < step);
  });

  const back = el('onbBack');
  if (back) back.hidden = step === 1;

  const next = el('onbNext');
  if (next) next.textContent = step === TOTAL_STEPS ? 'Открыть настройки' : 'Далее';

  disarmSkip();
}

/** Убрать подтверждение пропуска и вернуть обычный вид подвала. */
function disarmSkip() {
  skipArmed = false;
  const confirm = el('onbSkipConfirm');
  if (confirm) confirm.hidden = true;
  const skip = el('onbSkip');
  if (skip) {
    skip.hidden = false;
    skip.textContent = 'Пропустить';
  }
}

/** Показать онбординг принудительно (кнопка в настройках, повторный просмотр). */
export function startOnboarding() {
  const overlay = el('onbOverlay');
  if (!overlay) return;
  step = 1;
  render();
  overlay.classList.add('show');
}

/** Закрыть и больше не показывать сам по себе. */
export function closeOnboarding() {
  el('onbOverlay')?.classList.remove('show');
  disarmSkip();
  try {
    localStorage.setItem(SEEN_KEY, '1');
  } catch {
    // Приватный режим браузера запрещает запись. Не повод ронять вход в приложение:
    // в худшем случае подсказка появится снова при следующем входе.
  }
}

/** Показать один раз после входа. Вызывается из enterApp(). */
export function maybeShowOnboarding() {
  let seen = '1';
  try {
    seen = localStorage.getItem(SEEN_KEY);
  } catch {
    return; // нет доступа к хранилищу — лучше не показывать, чем показывать каждый раз
  }
  if (seen) return;
  startOnboarding();
}

/** Настройки + открытая инструкция по ключу: конец тура ведёт именно сюда. */
export function openKeyGuide() {
  closeOnboarding();
  try {
    goTo('settings');
  } catch {
    // Раздел мог быть переименован — подсказка не должна из-за этого падать.
  }
  const open = window.openModal;
  if (typeof open === 'function') open('modalKeyTutorial');
}

function nextStep() {
  if (step < TOTAL_STEPS) {
    step += 1;
    render();
    return;
  }
  // Последний шаг ведёт туда, где работа реально начинается: ключ YouTube API
  // в настройках. Без него поиск лидов вернёт ошибку на первом же запросе.
  openKeyGuide();
}

function prevStep() {
  if (step > 1) {
    step -= 1;
    render();
  }
}

/** Первый клик по «Пропустить» — предупреждение, второй уже закрывает. */
function requestSkip() {
  if (skipArmed) {
    closeOnboarding();
    return;
  }
  skipArmed = true;
  const confirm = el('onbSkipConfirm');
  if (confirm) confirm.hidden = false;
  const skip = el('onbSkip');
  if (skip) skip.hidden = true;
}

/* ------------------------------------------------------------------ */
/*  Кубики быстрого старта на Главной                                  */
/* ------------------------------------------------------------------ */

/** Отметить шаг выполненным по РЕАЛЬНЫМ данным (ключи, аккаунты, лиды, офферы).
 *  Никаких «галочек за красоту»: done ставится только по факту. */
export function setQuickStep(key, done) {
  const cube = document.querySelector(`.qs-cube[data-qs="${key}"]`);
  if (!cube) return;
  cube.classList.toggle('done', !!done);
  const state = cube.querySelector('.qs-cube-state');
  if (state && done) state.textContent = 'Готово';
  refreshQuickStart();
}

/** Скрыть блок, когда все четыре шага закрыты или человек скрыл его сам. */
function refreshQuickStart() {
  const box = el('quickStart');
  if (!box) return;
  let hidden = '';
  try {
    hidden = localStorage.getItem(QS_HIDDEN_KEY) || '';
  } catch {
    hidden = '';
  }
  const cubes = [...box.querySelectorAll('.qs-cube')];
  const allDone = cubes.length > 0 && cubes.every((cube) => cube.classList.contains('done'));
  box.hidden = !!hidden || allDone;
}

export function initQuickStart() {
  const box = el('quickStart');
  if (!box) return;
  el('quickStartHide')?.addEventListener('click', () => {
    try {
      localStorage.setItem(QS_HIDDEN_KEY, '1');
    } catch {
      // без хранилища блок просто вернётся после перезагрузки — не критично
    }
    box.hidden = true;
  });
  refreshQuickStart();
}

export function initOnboarding() {
  initQuickStart();

  const overlay = el('onbOverlay');
  if (!overlay) return;

  el('onbNext')?.addEventListener('click', nextStep);
  el('onbBack')?.addEventListener('click', prevStep);
  el('onbSkip')?.addEventListener('click', requestSkip);
  el('onbSkipYes')?.addEventListener('click', closeOnboarding);
  el('onbSkipNo')?.addEventListener('click', disarmSkip);

  // Клик по тёмному фону закрывает; клик внутри окна — нет.
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) closeOnboarding();
  });

  document.addEventListener('keydown', (e) => {
    if (!overlay.classList.contains('show')) return;
    if (e.key === 'Escape') closeOnboarding();
    if (e.key === 'ArrowRight') nextStep();
    if (e.key === 'ArrowLeft') prevStep();
  });
}
