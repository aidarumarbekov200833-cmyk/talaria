/** Переписка с владельцем сервиса («Поддержка»).
 *
 *  Это не чат Telegram и не форма «отправить и надеяться»: сообщение и ответ
 *  видны в одном диалоге внутри кабинета. Владелец получает уведомление ботом и
 *  отвечает из админки, ответ приходит человеку и в кабинет, и в Telegram.
 *
 *  Всё содержимое строится через textContent: в переписке бывают угловые скобки
 *  и ссылки, и подставлять её в innerHTML — прямой путь к XSS на своём же сайте.
 */
import { api } from './api.js';
import { openModal, toast } from './ui.js?v=tal32';
import { closeAccountMenu, paintSupportBadge, refreshSupportBadge } from './account.js?v=talaria15';

let loading = false;

function timeLabel(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  return sameDay
    ? date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      });
}

function bubble(message) {
  const row = document.createElement('div');
  row.className = `sup-msg ${message.author === 'admin' ? 'in' : 'out'}`;
  const text = document.createElement('div');
  text.className = 'sup-text';
  text.textContent = message.text;
  const meta = document.createElement('div');
  meta.className = 'sup-meta';
  const who = document.createElement('span');
  who.textContent = message.author === 'admin' ? 'Поддержка' : 'Вы';
  const time = document.createElement('time');
  time.textContent = timeLabel(message.created_at);
  meta.append(who, time);
  row.append(text, meta);
  return row;
}

function renderThread(messages) {
  const box = document.getElementById('supportThread');
  if (!box) return;
  box.textContent = '';
  if (!messages.length) {
    const empty = document.createElement('div');
    empty.className = 'sup-empty';
    empty.textContent =
      'Здесь пока пусто. Напишите вопрос — ответ придёт в этот диалог и в Telegram.';
    box.append(empty);
    return;
  }
  messages.forEach((message) => box.append(bubble(message)));
  box.scrollTop = box.scrollHeight;
}

export async function loadSupportThread() {
  if (loading) return;
  loading = true;
  const box = document.getElementById('supportThread');
  if (box && !box.childElementCount) {
    const stub = document.createElement('div');
    stub.className = 'sup-empty';
    stub.textContent = 'Загрузка…';
    box.append(stub);
  }
  try {
    const data = await api('GET', '/api/support/messages');
    renderThread(data.messages || []);
    // Диалог открыт — значит, ответы прочитаны: гасим точку, не дожидаясь
    // следующего опроса.
    paintSupportBadge(0);
  } catch (e) {
    if (box) {
      box.textContent = '';
      const error = document.createElement('div');
      error.className = 'sup-empty';
      error.textContent = `Не удалось загрузить переписку: ${e.message}`;
      box.append(error);
    }
  } finally {
    loading = false;
  }
}

export async function sendSupportMessage() {
  const field = document.getElementById('supportInput');
  const text = (field?.value || '').trim();
  if (!text) {
    toast('Напишите вопрос', 'warn');
    field?.focus();
    return;
  }
  const button = document.getElementById('supportSend');
  if (button) button.disabled = true;
  try {
    await api('POST', '/api/support/messages', { text });
    if (field) field.value = '';
    await loadSupportThread();
    toast('Отправлено — ответ придёт сюда и в Telegram', 'ok');
  } catch (e) {
    toast(e.message || 'Не удалось отправить', 'err');
  } finally {
    if (button) button.disabled = false;
  }
}

/** Открыть переписку с поддержкой. Зовётся из аккаунт-меню и из хелп-центра. */
export function openSupport() {
  // Панель аккаунта закрывается: она висит поверх модалок и перехватывала бы
  // нажатия в переписке (на телефоне — прямо по центру экрана).
  closeAccountMenu();
  openModal('modalSupport');
  loadSupportThread();
  setTimeout(() => document.getElementById('supportInput')?.focus(), 160);
}

export function initSupport() {
  // Точку на карточке аккаунта проверяем после входа и раз в пять минут: чаще
  // не нужно, ответ поддержки не срочнее этого.
  refreshSupportBadge();
  setInterval(refreshSupportBadge, 300_000);
}
