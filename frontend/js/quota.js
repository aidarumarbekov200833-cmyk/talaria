/** Отображение квоты YouTube API в сайдбаре.
 *
 *  Цифра — это наш счётчик, не данные от Google (у YouTube нет метода,
 *  который отдал бы остаток квоты — точное число только в консоли Cloud).
 */
import { api } from './api.js';
import { goTo } from './nav.js?v=zephyr4';

/** Подпись под полосой квоты.
 *
 *  Если ключей нет, «ключ YouTube API» — кликабельно и ведёт прямо к полю ввода
 *  ключа в настройках: раньше в сайдбаре в этом случае было «Потрачено 0 из 0
 *  баллов», и куда идти за ключом, человек не понимал.
 */
export function renderQuotaHint(hint, q) {
  if (!hint) return;
  hint.textContent = '';
  const hasKeys = Number(q.keys_enabled ?? 0) > 0 || Number(q.total_daily ?? 0) > 0;
  if (hasKeys) {
    // Ключ добавлен — длинная подсказка больше не нужна: цифры уже стоят строкой
    // выше. Просьба владельца: «сделай чтобы снизу текст уходил после
    // добавления ключа и занимало меньше текста».
    hint.hidden = true;
    return;
  }
  hint.hidden = false;
  hint.append(document.createTextNode('Добавьте '));
  const link = document.createElement('button');
  link.type = 'button';
  link.className = 'usage-hint-link';
  link.textContent = 'ключ YouTube API';
  link.addEventListener('click', () => {
    goTo('settings');
    setTimeout(() => {
      const field = document.getElementById('newKeyValue');
      field?.closest('.panel, .key-add')?.scrollIntoView({ block: 'center' });
      field?.focus();
    }, 220);
  });
  hint.append(link);
  hint.append(document.createTextNode(' — без него поиск не запустится.'));
}

/** Обновить виджет квоты в сайдбаре. */
export async function refreshQuota() {
  const bar = document.getElementById('usageBar');
  const text = document.getElementById('usageText');
  if (!bar || !text) return;
  try {
    const q = await api('GET', '/api/quota');
    const pct = q.total_daily > 0 ? Math.round((q.used_today / q.total_daily) * 100) : 0;
    bar.style.width = pct + '%';
    // Процент показывает, насколько заполнен день, остаток — сколько ещё можно
    // потратить. Одного числа мало: 62% ни о чём не говорит, пока не знаешь,
    // от какого объёма считается.
    const left = Math.max(0, Number(q.left_today ?? q.total_daily - q.used_today));
    text.textContent =
      q.total_daily > 0 ? `${pct}% · осталось ${left.toLocaleString('ru-RU')}` : 'ключей нет';
    renderQuotaHint(document.getElementById('usageHint'), q);
  } catch (e) {
    // Не блокируем работу, если квота не загрузилась.
    console.warn('Quota refresh failed:', e.message);
  }
}
