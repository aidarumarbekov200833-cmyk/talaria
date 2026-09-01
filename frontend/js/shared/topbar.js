/* v48: радужная полоса загрузки под верхом страницы.
   Просьба владельца: «как нажимаешь войти — сверху загрузка, полоска
   радужная, плавная». Полоса общая на все загрузки: счётчик pending
   позволяет нескольким запросам делить одну полосу — прячется она только
   когда закончил последний. Элемент #loadRail лежит в разметке страницы,
   стили — в talaria-v5.css (.load-rail). */

let pending = 0;
let hideTimer = null;

function rail() {
  /* В node-тестах DOM нет: модуль честно молчит, а не падает. */
  if (typeof document === 'undefined') return null;
  return document.getElementById('loadRail');
}

export function railStart() {
  const el = rail();
  if (!el) return;
  pending += 1;
  clearTimeout(hideTimer);
  el.classList.add('on');
  /* Недоезд до 80%: полоса живёт, пока идёт запрос, и доезжает до конца
     только на railDone — как в браузерах. */
  el.style.setProperty('--rail-p', '0.14');
  requestAnimationFrame(() => el.style.setProperty('--rail-p', '0.8'));
}

export function railDone() {
  const el = rail();
  if (!el) return;
  pending = Math.max(0, pending - 1);
  if (pending) return;
  el.style.setProperty('--rail-p', '1');
  hideTimer = setTimeout(() => {
    el.classList.remove('on');
    el.style.setProperty('--rail-p', '0');
  }, 500);
}
