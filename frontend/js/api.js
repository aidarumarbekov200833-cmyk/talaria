import { escapeHtml } from './shared/dom.js';
import { get, remove, set } from './shared/storage.js';

/** Запросы к API и хранение токена.
 *
 *  Ключ токена — mf_token. Раньше inline-скрипт писал в access_token,
 *  а модули читали mf_token: после входа запросы уходили без заголовка
 *  Authorization и бэкенд отвечал 401.
 */

export function getToken() {
  return get('mf_token');
}
export function setToken(t) {
  set('mf_token', t);
}
export function clearToken() {
  remove('mf_token');
  // Резервный админский токен режима «смотреть глазами пользователя»
  // (admin.js, VIEW_BACKUP_KEY). Конец сессии — по кнопке или по 401 — обязан
  // убирать и его: иначе полноценный админский JWT переживал бы logout и
  // доставался любому за этой машиной.
  remove('mf_token_admin');
}

/* --------------------------- единый перехват 401 ---------------------------
 *
 *  Раньше протухший токен выглядел как сломавшееся приложение: каждый раздел
 *  показывал свою ошибку «HTTP 401», а человек оставался на пустых экранах и не
 *  понимал, что надо просто войти заново.
 *
 *  Обработчик регистрируется извне (из auth.js), а не импортируется сюда:
 *  auth.js уже импортирует api.js, и обратный импорт дал бы цикл.
 */
let onUnauthorized = null;
let unauthorizedFired = false;

export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

/** Сброс после успешного входа — иначе второе протухание за сессию пройдёт молча. */
export function resetUnauthorized() {
  unauthorizedFired = false;
}

function handleUnauthorized() {
  // Страница грузит десяток запросов параллельно (loadUserData), и все они
  // упадут с 401 одновременно. Без флага человек получил бы десять одинаковых
  // тостов подряд и десять выходов из системы.
  if (unauthorizedFired || !onUnauthorized) return;
  unauthorizedFired = true;
  try {
    onUnauthorized();
  } catch (_) {}
}

function jsonHeaders(extra) {
  const h = Object.assign(
    {
      'Content-Type': 'application/json',
    },
    extra || {},
  );
  const token = getToken();
  if (token) h['Authorization'] = 'Bearer ' + token;
  return h;
}

/** Заголовки без Content-Type — для загрузки файлов и скачивания. */
function authHeaders() {
  const h = {};
  const token = getToken();
  if (token) h['Authorization'] = 'Bearer ' + token;
  return h;
}

/** Разбор ошибки ответа в читаемое сообщение. */
async function errorMessage(res) {
  let msg = `HTTP ${res.status}`;
  // 413 отдаёт сам nginx HTML-страницей, до JSON дело не доходит — без этой
  // ветки человек видел голое «HTTP 413» при слишком тяжёлых картинках оффера.
  if (res.status === 413) {
    return 'Вложения слишком большие: суммарно допустимо около 10 МБ. Уменьшите или уберите часть картинок';
  }
  try {
    const e = await res.json();
    // FastAPI кладёт текст в detail; у тела может быть любой тип
    // (строка, массив). Если есть — показываем его, а не "HTTP 401".
    if (e && e.detail) {
      msg = Array.isArray(e.detail)
        ? e.detail.map((d) => (d && d.msg ? d.msg : String(d))).join('; ')
        : String(e.detail);
    }
  } catch (_) {}
  return msg;
}

/** Общая проверка ответа: 401 — в перехватчик, остальное — в исключение. */
async function ensureOk(res) {
  if (res.ok) return;
  if (res.status === 401) handleUnauthorized();
  const err = new Error(await errorMessage(res));
  // Код ответа нужен вызывающим, чтобы отличать «токен мёртв» (401/403) от
  // «сервис прилёг» (5xx/сеть): реакции противоположные.
  err.status = res.status;
  throw err;
}

export async function api(method, path, body, options = {}) {
  const opts = { method, headers: jsonHeaders() };
  if (body) opts.body = JSON.stringify(body);
  if (options.signal) opts.signal = options.signal;
  const res = await fetch(path, opts);
  await ensureOk(res);
  return res.json();
}

/** Отправка файла (multipart/form-data).
 *
 *  Content-Type здесь не ставится намеренно: его проставляет браузер вместе
 *  с boundary. Задашь вручную — сервер не сможет разобрать тело запроса.
 */
export async function apiUpload(path, formData) {
  const res = await fetch(path, { method: 'POST', headers: authHeaders(), body: formData });
  await ensureOk(res);
  return res.json();
}

/** Скачивание файла с авторизацией.
 *
 *  Простой <a href> здесь не работает: токен живёт в localStorage и уходит
 *  заголовком, а не cookie, поэтому браузерное скачивание получило бы 401.
 *  Имя файла берём из Content-Disposition, если сервер его прислал.
 */
export async function apiDownload(path, fallbackName) {
  const res = await fetch(path, { method: 'GET', headers: authHeaders() });
  await ensureOk(res);

  let name = fallbackName || 'export.csv';
  const disp = res.headers.get('Content-Disposition') || '';
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disp);
  if (match) {
    try {
      name = decodeURIComponent(match[1]);
    } catch (_) {
      name = match[1];
    }
  }

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Отпускаем память не сразу: Safari отменяет скачивание, если revokeObjectURL
  // вызвать в той же синхронной строке.
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  return name;
}

/** Escape untrusted strings for HTML embedding. */
export function esc(s) {
  return escapeHtml(s);
}
