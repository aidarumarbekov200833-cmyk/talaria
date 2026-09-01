/** ИИ-помощник: реальный чат через POST /api/ai/chat.
 *
 *  Раньше здесь были захардкоженные ответы (demo.js). Теперь история сообщений
 *  живёт на клиенте и целиком уходит на сервер, а сервер проксирует её в
 *  OpenAI-совместимый API (base_url/ключ/модель из настроек) и, если включены
 *  инструменты, выполняет их над настоящими данными. Выдуманных ответов нет:
 *  ошибку сервера показываем текстом ответа, без выдуманных советов.
 */
import { api, getToken } from './api.js';
import { goTo } from './nav.js?v=zephyr4';
import { setTheme } from './theme.js?v=zephyr3';
import { loadOffers } from './offers.js?v=aioffer1';
import { applyAiSearchPrefill } from './search.js?v=talaria19';

// История для контекста. Системный промпт добавляет сервер — здесь только диалог.
const history = [];
let busy = false;

/** Добавить сообщение в DOM. role: 'user' | 'ai' | 'tool' | 'error'.
 *
 *  У каждого сообщения — время, как в мессенджере: в длинной переписке с
 *  помощником иначе непонятно, ответ свежий или остался с прошлого захода.
 */
function bubble(role, text) {
  const body = document.getElementById('aiBody');
  if (!body) return null;
  const el = document.createElement('div');
  el.className = 'msg ' + role;
  const line = document.createElement('span');
  line.className = 'msg-text';
  line.textContent = text; // textContent: не даём разметке из ответа исполниться
  const time = document.createElement('time');
  time.className = 'msg-time';
  time.textContent = new Date().toLocaleTimeString('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
  });
  el.append(line, time);
  body.appendChild(el);
  body.scrollTop = body.scrollHeight;
  return line;
}

/** Пузырь «думает»: три точки, которые дышат (CSS-анимация aiDots).
 *  Раньше здесь стоял статичный символ «…» — выглядело как зависший ответ. */
function thinkingBubble() {
  const body = document.getElementById('aiBody');
  if (!body) return null;
  const el = document.createElement('div');
  el.className = 'msg ai thinking';
  el.setAttribute('aria-label', 'ИИ печатает');
  el.innerHTML = '<i></i><i></i><i></i>';
  body.appendChild(el);
  body.scrollTop = body.scrollHeight;
  return el;
}

const SAFE_SECTIONS = new Set([
  'dashboard',
  'search',
  'databases',
  'offer',
  'mailing',
  'messages',
  'tariffs',
  'settings',
  'admin',
]);

function setFieldValue(id, value) {
  const field = document.getElementById(id);
  if (!field || value === undefined || value === null) return;
  field.value = String(value);
  field.dispatchEvent(new Event('input', { bubbles: true }));
  field.dispatchEvent(new Event('change', { bubbles: true }));
}

/** Execute only the server-validated ui_action returned by ai_tools.py.
 *  Raw model arguments are never trusted. These actions are reversible and do
 *  not click Save, Search, Launch or Delete buttons. */
function applyUiAction(call) {
  const action = call?.result?.ui_action;
  if (!action || typeof action !== 'object') return;

  if (action.action === 'open_section' && SAFE_SECTIONS.has(action.section)) {
    goTo(action.section);
    // Помощник умеет сохранять офферы сам. Без перезагрузки списка человек
    // увидел бы старый набор и решил, что сохранение не сработало.
    if (action.section === 'offer' && call?.name === 'save_offer') {
      loadOffers().catch(() => {});
    }
    return;
  }

  if (action.action === 'prefill_search') {
    goTo('search');
    // Фильтры поиска с v38 живут в двух режимах, а страна — в мультиселекте,
    // поэтому запись в поля напрямую больше не работает: переключение режима и
    // отрисовку берёт на себя сам раздел.
    applyAiSearchPrefill(action);
    return;
  }

  if (action.action === 'draft_offer') {
    goTo('offer');
    document.querySelector('[data-offer-tab="manual"]')?.click();
    setFieldValue('offerNameInput', String(action.offer_name || '').slice(0, 200));
    setFieldValue('offerTextInput', String(action.offer_text || '').slice(0, 5000));
    document.getElementById('offerNameInput')?.focus();
    return;
  }

  if (action.action === 'set_appearance') {
    if (['light', 'dark'].includes(action.theme)) setTheme(action.theme);
  }
}

export async function sendAi() {
  const inp = document.getElementById('aiInput');
  if (!inp) return;
  const text = inp.value.trim();
  if (!text || busy) return;

  if (!getToken()) {
    bubble('error', 'Сначала войдите в систему.');
    return;
  }

  bubble('user', text);
  history.push({ role: 'user', content: text });
  if (history.length > 24) history.splice(0, history.length - 24);
  inp.value = '';

  busy = true;
  const thinking = thinkingBubble();
  try {
    const res = await api('POST', '/api/ai/chat', { messages: history });
    if (thinking) thinking.remove();
    // Список вызванных инструментов больше не печатаем перед ответом
    // (Samat: «когда говорю что-то сделать, в начале какой-то текст — убери»).
    // Сами действия по-прежнему выполняются, просто без служебной строки.
    (res.tool_calls || []).forEach(applyUiAction);
    const reply = res.reply || '(пустой ответ)';
    bubble('ai', reply);
    history.push({ role: 'assistant', content: reply });
    if (history.length > 24) history.splice(0, history.length - 24);
  } catch (e) {
    if (thinking) thinking.remove();
    const msg = e.message || 'Ошибка запроса';
    bubble('error', msg);
  } finally {
    busy = false;
  }
}
