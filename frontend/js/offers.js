/** Офферы (креативы): загрузка из API, рендер, создание, редактирование, удаление.
 *
 *  Раньше эта логика была разделена между inline-скриптом (локальные функции,
 *  которые только добавляли карточку в DOM) и features.js (реальные запросы).
 *  Inline-версия перезаписывала API-версию, поэтому офферы не сохранялись.
 *  Здесь один путь: всё идёт через бэкенд.
 *
 *  Плейсхолдеры ({channel}, {first_name} …) подставляют настоящие поля канала и
 *  Telegram-профиля лида. Токены, для которых данных ещё нет, бэкенд помечает
 *  недоступными — мы показываем их выключенными, а не подставляем выдумку.
 */
import { api, getToken, esc } from './api.js';
import { toast, closeModal, confirmAction } from './ui.js?v=tal32';
import { setCampaignOffers } from './campaigns.js?v=zephyr3';
import { logLine, resetLog } from './logline.js?v=tal33';

const OFFERS = [];
let editingOfferId = null;
let placeholders = []; // из GET /api/offers/placeholders
// Картинка со страницы хранится как data-URL: бэкенд принимает строку, а файл
// на диск мы не кладём — оффер должен уехать в Telegram целиком, одним объектом.
let pageImage = '';
// Альбом страницы: до 10 кадров. pageImage остаётся первым — на него смотрят
// предпросмотр Telegram и старый код сохранения.
let pageAlbum = [];
const MAX_ALBUM = 10;
// Автопредпросмотр: таймер гасит дрожь набора, счётчик — гонку ответов:
// без него медленный ответ на старый текст перезатирал бы свежий.
let previewTimer = null;
let previewSeq = 0;

const EDIT_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>';
const TRASH_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>';

// Тот же лимит, что и на бэкенде (max_offer_image_bytes ≈ 600 KB после base64).
// Проверяем до отправки, иначе человек ждёт загрузку и получает 413.
const MAX_IMAGE_BYTES = 800_000;
const ALLOWED_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);

/** Карточка оффера. Все значения из API проходят через esc(), иначе кавычка
 *  в названии или ссылке закрывает атрибут и ломает разметку. */
function offerCardHtml(o, withActions) {
  const album = Array.isArray(o.images) ? o.images.filter(Boolean) : [];
  const img = o.image
    ? `<img class="offer-img-preview has-img" src="${esc(o.image)}" alt="Превью" style="display:block">${
        album.length > 1 ? `<span class="offer-album-badge">${album.length} фото</span>` : ''
      }`
    : '';
  const actions = withActions
    ? `<div class="row-actions">
         <button class="mini-btn" data-tip="Редактировать" data-edit-offer="${o.id}">${EDIT_SVG}</button>
         <button class="mini-btn" data-tip="Удалить" data-remove-offer="${o.id}">${TRASH_SVG}</button>
       </div>`
    : '';
  // Даты создания достаточно: счётчики отправок/конверсии врать не будем,
  // пока рассылка не отправляет сообщения из интерфейса.
  const when = o.created_at
    ? new Date(o.created_at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
    : '';
  // Текст, написанный помощником, помечаем явно: человек не должен разослать
  // сотне людей письмо, которое он принял за своё.
  const byAi = o.created_by === 'ai';
  const badge = byAi
    ? '<span class="badge on offer-badge-ai" data-tip="Текст написал ИИ-помощник — проверьте перед рассылкой">Оффер от ИИ</span>'
    : '<span class="badge on">Оффер</span>';
  return `<div class="offer-top">${badge}${actions}</div>
    <div class="row-title" style="margin-top:8px;font-size:15px;">${esc(o.name)}</div>
    ${img}
    <div class="offer-desc" style="margin-top:12px;">${esc(o.text) || 'Без текста'}</div>
    <div class="offer-meta" style="margin-top:auto;padding-top:12px;"><span>${when}</span></div>`;
}

export async function loadOffers() {
  if (!getToken()) return;
  try {
    const data = await api('GET', '/api/offers');
    OFFERS.length = 0;
    OFFERS.push(...(data.offers || []));
    renderOffers();
  } catch (e) {
    console.log('Не удалось загрузить офферы:', e.message);
  }
  if (!placeholders.length) loadPlaceholders();
}

export function renderOffers() {
  const grid = document.getElementById('offerGrid');
  if (!grid) return;
  grid.innerHTML = '';

  if (!OFFERS.length) {
    grid.innerHTML = '<div class="empty-state show">Пока нет офферов — создайте первый</div>';
  } else {
    OFFERS.forEach((o) => {
      const card = document.createElement('div');
      card.className = 'offer-card';
      card.innerHTML = offerCardHtml(o, true);
      grid.appendChild(card);
    });
  }

  renderOfferOptions();
}

/** Рассылке нужен не список офферов, а их количество: офферы там не
 *  выбираются — в дело идут все и чередуются по лидам. */
function renderOfferOptions() {
  setCampaignOffers(OFFERS);
}

/* ---------- ЛОГИ ПРОЦЕССА ---------- */

/** Пишет строку в терминал справа от редактора.
 *  Вид тот же, что на «Поиске» и «Рассылке»: три разных оформления журнала на
 *  одном сайте — это не три фичи, а три места, где надо чинить одно и то же. */
export function offerLog(message, level = 'info') {
  logLine('offerLogs', message, level);
}

export function clearOfferLog() {
  resetLog('offerLogs', 'Пока пусто — здесь появится, что сохранилось и что ответил ИИ.');
}

/* ---------- ПОДСТАНОВКИ ---------- */

/** Тянет список плейсхолдеров с бэкенда. Локального списка держать нельзя:
 *  разойдётся с тем, что реально умеет подставлять сервер. */
export async function loadPlaceholders() {
  const box = document.getElementById('placeholderChips');
  if (!box || !getToken()) return;
  try {
    const data = await api('GET', '/api/offers/placeholders');
    placeholders = data.placeholders || [];
    renderPlaceholderChips();
  } catch (e) {
    box.innerHTML = `<span class="ph-hint">Не удалось загрузить подстановки: ${esc(e.message)}</span>`;
  }
}

function renderPlaceholderChips() {
  const box = document.getElementById('placeholderChips');
  if (!box) return;
  box.innerHTML = '';

  placeholders.forEach((p) => {
    const chip = document.createElement('span');
    chip.className = 'ph-chip' + (p.available ? '' : ' off');
    // Раньше в чипе был только токен ({channel}) — по нему непонятно, что
    // подставится. Теперь рядом человеческое название поля, а пример остаётся
    // в подсказке: «что это» видно сразу, без наведения.
    const tokenNode = document.createElement('b');
    tokenNode.textContent = p.token;
    const titleNode = document.createElement('i');
    titleNode.textContent = p.available ? p.title : `${p.title} — нет данных`;
    chip.append(tokenNode, titleNode);
    // Источник подсказываем прямо в тултипе: имя и фамилия берутся из Telegram,
    // а не из YouTube, и без этой пометки непонятно, откуда они вообще возьмутся.
    const from = p.source === 'telegram' ? ' · из Telegram-профиля' : '';
    // Своя подсказка (data-tip), а не системный title: системная рисуется
    // браузером и на тёмной теме выглядит белым пятном поверх контента.
    chip.dataset.tip = p.available
      ? `${p.title}${from}${p.example ? ' · например: ' + p.example : ''}`
      : `${p.title} — недоступно. ${p.note}`;
    if (p.available) {
      chip.dataset.token = p.token;
    } else {
      chip.dataset.unavailable = p.note || 'Данных для этого поля пока нет.';
    }
    box.appendChild(chip);
  });
}

/** Вставляет токен на место курсора, а не в конец: иначе при правке середины
 *  текста подстановка улетала в самый низ. */
export function insertPlaceholder(token) {
  const area = document.getElementById('offerTextInput');
  if (!area) return;
  const start = area.selectionStart ?? area.value.length;
  const end = area.selectionEnd ?? area.value.length;
  area.value = area.value.slice(0, start) + token + area.value.slice(end);
  const at = start + token.length;
  area.setSelectionRange(at, at);
  area.focus();
  schedulePreview();
}

function showPlaceholderNote(text) {
  const note = document.getElementById('placeholderNote');
  if (!note) return;
  note.textContent = text;
  note.style.display = text ? 'block' : 'none';
}

/* ---------- ПРЕДПРОСМОТР В ВИДЕ ЧАТА TELEGRAM ---------- */

/** Подставить примеры вместо тех плейсхолдеров, для которых данных лида нет. */
function fillExamples(text) {
  return String(text || '').replace(/\{([a-zA-Z_]+)\}/g, (whole, token) => {
    const ph = placeholders.find((p) => p.token === `{${token}}`);
    return ph && ph.example ? ph.example : whole;
  });
}

/** Окно чата под логами обновляется само — кнопки «Предпросмотр» больше нет:
 *  человек пишет текст и сразу видит, что придёт лиду после отправки.
 *  Если в тексте есть подстановки — просим бэкенд подставить данные
 *  настоящего лида; без подстановок сервер не нужен — текст уйдёт как есть.
 *  silent: обновление «на лету» не сыплет тостами на каждую букву. */
export async function previewOffer(opts = {}) {
  const silent = opts.silent === true;
  const area = document.getElementById('offerTextInput');
  if (!area) return;
  const text = area.value;

  if (!text.trim()) {
    renderTgPreview('', '', [], { empty: true });
    return;
  }

  // Подстановки вида {channel}: только из-за них имеет смысл запрос к API.
  const hasTokens = /\{[a-zA-Z_]+\}/.test(text);
  if (!hasTokens) {
    renderTgPreview(text, '', [], { local: true });
    return;
  }
  if (!getToken()) {
    // Без входа реального лида нет, но показывать фигурные скобки всё равно нельзя.
    renderTgPreview(fillExamples(text), '', [], { examples: true, noAuth: true });
    return;
  }

  const seq = ++previewSeq;
  try {
    const res = await api('POST', '/api/offers/preview', { text });
    if (seq !== previewSeq) return;
    const unresolved = res.unresolved || [];
    // Остаток добиваем примерами: в окне «как будет показываться» должен быть
    // итоговый текст, а не технические скобки.
    renderTgPreview(fillExamples(res.text), res.lead_title || '', unresolved);
  } catch (e) {
    if (seq !== previewSeq) return;
    renderTgPreview(fillExamples(text), '', [], { examples: true, error: e.message });
    if (!silent) {
      toast(`Не удалось подставить данные лида: ${e.message}`, 'warn');
      offerLog(`Предпросмотр не собрался: ${e.message}`, 'error');
    }
  }
}

/** Отложенный вызов: на каждое нажатие клавиши в API не ходим. */
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => previewOffer({ silent: true }), 500);
}

/** Отрисовка окна чата. Вынесена отдельно, потому что её зовёт и набор текста,
 *  и ИИ-вкладка — оба показывают одно и то же окно. */
function renderTgPreview(text, leadTitle, unresolved, opts = {}) {
  const box = document.getElementById('offerTgPreview');
  if (!box) return;

  const name = leadTitle || 'Лид';
  document.getElementById('tgPreviewName').textContent = name;
  document.getElementById('tgPreviewAva').textContent = name.trim().charAt(0).toUpperCase() || 'Л';

  // Подпись честно говорит, откуда взялись значения: без неё непонятно,
  // почему {channel} в окне остался текстом.
  let sub = 'текст и картинка берутся из конструктора выше';
  if (opts.empty) sub = 'напишите текст — окно обновится само';
  else if (leadTitle) sub = `на реальном лиде: ${leadTitle}`;
  else if (opts.noAuth)
    sub = 'показаны примерные значения — войдите, чтобы увидеть данные реального лида';
  else if (opts.error) sub = 'подставлены примеры — данные лида не загрузились';
  else if (opts.local) sub = 'подстановок в тексте нет — уйдёт ровно так';
  document.getElementById('tgPreviewLead').textContent = sub;

  const body = document.getElementById('tgPreviewText');
  body.textContent = opts.empty ? 'Напишите текст оффера выше — он сразу появится здесь.' : text;
  body.classList.toggle('muted', !!opts.empty);

  document.getElementById('tgPreviewTime').textContent = new Date().toLocaleTimeString('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
  });

  const img = document.getElementById('tgPreviewImg');
  if (pageImage && !opts.empty) {
    img.src = pageImage;
    img.style.display = 'block';
  } else {
    img.removeAttribute('src');
    img.style.display = 'none';
  }

  const warn = document.getElementById('tgPreviewWarn');
  if (unresolved && unresolved.length) {
    warn.textContent =
      `Здесь показаны примеры для: ${unresolved.join(', ')}. У этого лида таких данных нет — ` +
      'проверьте текст, чтобы без них сообщение оставалось осмысленным.';
    warn.style.display = 'block';
  } else {
    warn.style.display = 'none';
  }
}

/* ---------- ВКЛАДКИ ---------- */

function switchOfferTab(tab) {
  document.querySelectorAll('.offer-tab').forEach((b) => {
    b.classList.toggle('active', b.dataset.offerTab === tab);
  });
  document.getElementById('offerPaneManual')?.classList.toggle('active', tab === 'manual');
  document.getElementById('offerPaneAi')?.classList.toggle('active', tab === 'ai');
  if (tab === 'ai') loadAiOfferSettings();
}

/* ---------- ИИ-ОФФЕР ---------- */

/*  Вкладка «ИИ-оффер» — это настройка, а не генератор одного текста.
 *
 *  Человек описывает скелет («примерный вид» письма) и сохраняет его. Дальше в
 *  рассылке достаточно переключателя «обычный оффер / ИИ-оффер»: ИИ сканирует
 *  контент каждого канала и по скелету пишет каждому лиду свой текст.
 *
 *  Чего здесь больше нет и почему: выбора лида (в рассылке лидов сотни, и текст
 *  нужен каждому свой), тона и поля «что мы предлагаем» — и то и другое человек
 *  и так пишет словами в скелете, а две настройки об одном давали модели
 *  противоречивые инструкции.
 */

let aiSettings = {
  template: '',
  materials: 5,
  allowed: false,
  plan_names: '',
  provider_ready: false,
};
let aiSettingsLoaded = false;
let aiPreviewVariants = [];

function paintAiSettings() {
  const area = document.getElementById('aiOfferTemplate');
  const materials = document.getElementById('aiOfferMaterials');
  const status = document.getElementById('aiOfferStatus');
  const badge = document.getElementById('aiOfferPlanBadge');
  if (area && document.activeElement !== area) area.value = aiSettings.template || '';
  if (materials) materials.value = String(aiSettings.materials || 5);

  if (badge) {
    badge.hidden = aiSettings.allowed;
    badge.className = 'badge warn';
    badge.textContent = `Тариф ${aiSettings.plan_names || 'выше'}`;
  }
  if (status) {
    // Честно перечисляем всё, что мешает: тариф и настройка провайдера — разные
    // причины, и «ИИ недоступен» без уточнения ничего человеку не говорит.
    const problems = [];
    if (!aiSettings.allowed)
      problems.push(`в рассылке доступен на тарифе ${aiSettings.plan_names || 'выше'}`);
    if (!aiSettings.provider_ready) problems.push('провайдер ИИ пока не настроен');
    status.textContent = problems.length
      ? `Настроить можно уже сейчас, но ${problems.join('; ')}.`
      : aiSettings.template
        ? 'Готово — можно выбирать «ИИ-оффер» в рассылке.'
        : 'Напишите скелет и сохраните.';
  }
}

/** Настройки ИИ-оффера с сервера. Зовём при открытии вкладки и после входа. */
export async function loadAiOfferSettings(force) {
  if (!getToken()) {
    const status = document.getElementById('aiOfferStatus');
    if (status) status.textContent = 'Войдите, чтобы настроить ИИ-оффер.';
    return aiSettings;
  }
  if (aiSettingsLoaded && !force) {
    paintAiSettings();
    return aiSettings;
  }
  try {
    aiSettings = await api('GET', '/api/offers/ai-settings');
    aiSettingsLoaded = true;
  } catch (e) {
    offerLog(`Не удалось загрузить настройки ИИ-оффера: ${e.message}`, 'warn');
  }
  paintAiSettings();
  return aiSettings;
}

/** Скелет, который сохранён на сервере — его показывает и форма рассылки. */
export function aiOfferSettings() {
  return aiSettings;
}

export async function saveAiOfferSettings() {
  if (!getToken()) {
    toast('Войдите, чтобы сохранить настройку', 'warn');
    return;
  }
  const template = (document.getElementById('aiOfferTemplate')?.value || '').trim();
  if (!template) {
    toast('Напишите скелет оффера', 'warn');
    return;
  }

  const materials = Number(document.getElementById('aiOfferMaterials')?.value) || 5;
  try {
    aiSettings = await api('PUT', '/api/offers/ai-settings', { template, materials });
    aiSettingsLoaded = true;
    paintAiSettings();
    toast('Настройка ИИ-оффера сохранена', 'success');
    offerLog(
      `Скелет ИИ-оффера сохранён · сканируем ${aiSettings.materials} последних материалов`,
      'success',
    );
    // Форма рассылки показывает этот же скелет — обновляем её сразу.
    document.dispatchEvent(new CustomEvent('mf:aioffer-saved', { detail: aiSettings }));
  } catch (e) {
    toast(`Не удалось сохранить: ${e.message}`, 'warn');
    offerLog(`Ошибка сохранения настройки ИИ-оффера: ${e.message}`, 'error');
  }
}

/** Проверка на самом свежем лиде: тот же путь, которым пойдёт рассылка. */
export async function generateAiOffer() {
  if (!getToken()) {
    toast('Войдите, чтобы пользоваться ИИ-оффером', 'warn');
    return;
  }

  const template = (document.getElementById('aiOfferTemplate')?.value || '').trim();
  if (!template) {
    toast('Сначала напишите скелет оффера', 'warn');
    return;
  }
  const materials = Number(document.getElementById('aiOfferMaterials')?.value) || 5;

  const btn = document.querySelector('[data-act="generateAiOffer"]');
  offerLog(`ИИ сканирует ${materials} последних материалов свежего лида…`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Пишем…';
  }

  try {
    const res = await api('POST', '/api/offers/ai-generate', { template, materials });
    renderAiOfferResult(res);
    offerLog(`ИИ написал оффер по каналу «${res.lead_title || '—'}»`, 'success');
    if (res.note) offerLog(res.note, 'warn');
    // Показываем в том же окне чата, что и обычный оффер: результат надо
    // сравнивать с тем, как выглядит письмо у лида, а не читать в сырой рамке.
    renderTgPreview(res.text, res.lead_title || '', []);
  } catch (e) {
    toast(`ИИ не написал оффер: ${e.message}`, 'warn');
    offerLog(`ИИ не написал оффер: ${e.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Сгенерировать 3 вступления';
    }
  }
}

/** Что именно ИИ прочитал — чтобы было видно, на чём основан текст,
 *  а не «модель что-то придумала». */
function renderAiOfferResult(res) {
  const box = document.getElementById('aiOfferResult');
  if (!box) return;
  const items = (res.analyzed || []).map((t) => `<li>${esc(t)}</li>`).join('');
  const note = res.note ? `<div class="ai-offer-note">${esc(res.note)}</div>` : '';
  const head = res.lead_title
    ? `Основа персонализации для «${esc(res.lead_title)}»:`
    : 'ИИ прочитал:';
  aiPreviewVariants = Array.isArray(res.variants) ? res.variants : [];
  const variants = aiPreviewVariants
    .map(
      (variant, index) => `
    <button type="button" class="ai-offer-variant${index === 0 ? ' active' : ''}" data-ai-variant="${index}">
      <span><b>${esc(variant.label || `Вариант ${index + 1}`)}</b><small>${esc(variant.angle || '')}</small></span>
      <em>${esc(variant.text || '')}</em>
    </button>`,
    )
    .join('');
  const facts = items
    ? `<div class="ai-offer-head">${head}</div><ul class="ai-offer-list">${items}</ul>`
    : '<div class="ai-offer-head">Материалов для анализа не нашлось.</div>';
  box.innerHTML = `${facts}${variants ? `<div class="ai-offer-variants"><div class="ai-offer-head">Варианты вступления</div>${variants}</div>` : ''}${note}`;
  box.style.display = 'block';
}

/* ---------- CRUD ---------- */

export function openOfferEdit(id) {
  const o = OFFERS.find((x) => x.id === id);
  if (!o) return;
  editingOfferId = id;
  document.getElementById('offerName').value = o.name;
  document.getElementById('offerDesc').value = o.text || '';
  const prev = document.getElementById('modalImgPreview');
  if (o.image) {
    prev.src = o.image;
    prev.classList.add('has-img');
    prev.style.display = 'block';
  }
  // Альбом оффера открываем в конструкторе на странице: в модалке для десяти
  // кадров нет места, а терять их при правке нельзя.
  pageAlbum = Array.isArray(o.images) ? o.images.filter(Boolean).slice(0, MAX_ALBUM) : [];
  syncAlbumToPreview();
  document.querySelector('#modalOffer .modal-head h3').textContent = 'Редактировать оффер';
  document.getElementById('modalOffer').classList.add('show');
}

export async function removeOffer(id) {
  const gone = OFFERS.find((x) => x.id === id);
  if (
    !(await confirmAction(
      `Удалить оффер «${gone ? gone.name : id}»? Это действие нельзя отменить.`,
    ))
  )
    return;
  try {
    await api('DELETE', `/api/offers/${id}`);
    const index = OFFERS.findIndex((item) => item.id === id);
    if (index >= 0) OFFERS.splice(index, 1);
    renderOffers();
    toast('Оффер удалён', 'info');
    offerLog(`Удалён оффер «${gone ? gone.name : id}»`, 'warn');
  } catch (e) {
    toast(`Не удалось удалить оффер: ${e.message}`, 'warn');
    offerLog(`Ошибка удаления: ${e.message}`, 'error');
  }
}

/** Создание/обновление оффера из модалки. */
export async function createOffer() {
  const name = document.getElementById('offerName').value.trim();
  const text = document.getElementById('offerDesc').value.trim();
  const prev = document.getElementById('modalImgPreview');
  const image = prev.classList.contains('has-img') ? prev.src : '';
  if (!name) {
    toast('Укажите название оффера', 'warn');
    return;
  }

  // Раньше без токена рисовалась карточка «сохранено (демо)» — оффера при этом
  // не существовало, и он пропадал при перезагрузке. Теперь честный отказ.
  if (!getToken()) {
    toast('Войдите, чтобы сохранить оффер', 'warn');
    return;
  }

  try {
    const editing = editingOfferId;
    const saved = await api(
      editing ? 'PATCH' : 'POST',
      editing ? `/api/offers/${editing}` : '/api/offers',
      { name, text, image },
    );
    const index = OFFERS.findIndex((item) => item.id === saved.id);
    if (index >= 0) OFFERS.splice(index, 1, saved);
    else OFFERS.unshift(saved);
    renderOffers();
    editingOfferId = null;
    closeModal('modalOffer');
    toast(`Оффер «${name}» сохранён`, 'success');
    offerLog(`${editing ? 'Изменён' : 'Создан'} оффер «${name}»`, 'success');
    // В ленту «Главной» событие кладёт бэкенд (log_event) — клиент не дублирует.
  } catch (e) {
    toast(`Ошибка сохранения оффера: ${e.message}`, 'warn');
    offerLog(`Ошибка сохранения «${name}»: ${e.message}`, 'error');
  }
}

/** Создание оффера с формы на странице (не из модалки). */
export async function createOfferFromPage() {
  const name = document.getElementById('offerNameInput').value.trim();
  const text = document.getElementById('offerTextInput').value.trim();
  if (!name) {
    toast('Укажите название оффера', 'warn');
    return;
  }
  if (!getToken()) {
    toast('Войдите, чтобы сохранить оффер', 'warn');
    return;
  }

  const button = document.querySelector('[data-act="createOfferFromPage"]');
  const buttonLabel = button?.textContent || 'Сохранить оффер';
  if (button) {
    button.disabled = true;
    button.textContent = 'Сохраняем…';
  }
  try {
    const saved = await api('POST', '/api/offers', {
      name,
      text,
      image: pageImage,
      images: pageAlbum,
    });
    OFFERS.unshift(saved);
    renderOffers();
    clearOfferForm();
    toast(`Оффер «${name}» сохранён`, 'success');
    offerLog(`Создан оффер «${name}»`, 'success');
  } catch (e) {
    toast(`Ошибка сохранения оффера: ${e.message}`, 'warn');
    offerLog(`Ошибка сохранения «${name}»: ${e.message}`, 'error');
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = buttonLabel;
    }
  }
}

export function clearOfferForm() {
  document.getElementById('offerNameInput').value = '';
  document.getElementById('offerTextInput').value = '';
  const file = document.getElementById('offerImageFile');
  if (file) file.value = '';
  pageImage = '';
  pageAlbum = [];
  paintOfferImageDraft('', '');
  paintOfferAlbum();
  const preview = document.getElementById('offerTgPreview');
  if (preview) preview.style.display = 'none';
  showPlaceholderNote('');
  previewOffer({ silent: true });
}

/** Прочитать один файл в data-URL с проверками формата и размера. */
function readImageFile(file) {
  return new Promise((resolve) => {
    if (!ALLOWED_IMAGE_TYPES.has(file.type)) {
      toast(`«${file.name}»: поддерживаются PNG, JPEG, WebP и GIF`, 'warn');
      resolve('');
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      toast(`«${file.name}» больше 800 КБ — не приложена`, 'warn');
      offerLog(`Картинка «${file.name}» больше 800 КБ — не приложена`, 'warn');
      resolve('');
      return;
    }
    const reader = new FileReader();
    reader.onload = (event) => resolve(String(event.target.result || ''));
    reader.onerror = () => resolve('');
    reader.readAsDataURL(file);
  });
}

/** Перерисовать плитки альбома под загрузчиком. */
function paintOfferAlbum() {
  const box = document.getElementById('offerAlbum');
  if (!box) return;
  box.hidden = pageAlbum.length < 1;
  box.innerHTML = pageAlbum
    .map(
      (src, index) =>
        `<div class="offer-album-item"><img src="${esc(src)}" alt="Кадр ${index + 1}"><button type="button" class="offer-album-del" data-album-remove="${index}" aria-label="Убрать кадр ${index + 1}">✕</button>${index === 0 ? '<span class="offer-album-cover">обложка</span>' : ''}</div>`,
    )
    .join('');
}

function syncAlbumToPreview() {
  pageImage = pageAlbum[0] || '';
  paintOfferImageDraft(pageImage, pageAlbum.length > 1 ? `альбом из ${pageAlbum.length}` : '');
  paintOfferAlbum();
  const img = document.getElementById('tgPreviewImg');
  if (img) {
    img.src = pageImage;
    img.style.display = pageImage ? 'block' : 'none';
  }
}

function paintOfferImageDraft(src, name) {
  const stage = document.querySelector('.offer-upload');
  const image = document.getElementById('offerImageDraftPreview');
  if (!stage || !image) return;
  image.src = src || '';
  image.alt = src
    ? `Предпросмотр: ${name || 'изображение оффера'}`
    : 'Предпросмотр загруженного изображения';
  stage.classList.toggle('has-image', !!src);
}

/** Картинка из модалки оффера. */
export function previewOfferImage(input) {
  const preview = document.getElementById('modalImgPreview');
  if (!input.files || !input.files[0]) return;
  if (!ALLOWED_IMAGE_TYPES.has(input.files[0].type)) {
    toast('Поддерживаются PNG, JPEG, WebP и GIF', 'warn');
    input.value = '';
    return;
  }
  if (input.files[0].size > MAX_IMAGE_BYTES) {
    toast('Картинка слишком большая — до 800 КБ', 'warn');
    input.value = '';
    return;
  }
  const reader = new FileReader();
  reader.onload = (e) => {
    preview.src = e.target.result;
    preview.style.display = 'block';
    preview.classList.add('has-img');
  };
  reader.readAsDataURL(input.files[0]);
}

/* Экспорт лидов в CSV переехал в databases.js: он выгружает данные из API,
   а не соскабливает уже отрисованную страницу (раньше в файл попадали только
   50 видимых строк, да и селекторы разошлись с новой таблицей). */

export function initOffers() {
  // Окно «Как сообщение придёт в Telegram» стоит под логами и видно всегда,
  // поэтому рисуем его сразу и обновляем по ходу набора текста.
  const offerText = document.getElementById('offerTextInput');
  if (offerText) {
    offerText.addEventListener('input', schedulePreview);
    previewOffer({ silent: true });
  }

  // Делегирование вместо inline-onclick с подстановкой id.
  const grid = document.getElementById('offerGrid');
  if (grid)
    grid.addEventListener('click', (e) => {
      const edit = e.target.closest('[data-edit-offer]');
      if (edit) {
        openOfferEdit(Number(edit.dataset.editOffer));
        return;
      }
      const del = e.target.closest('[data-remove-offer]');
      if (del) removeOffer(Number(del.dataset.removeOffer));
    });

  // Картинки со страницы: читаем файлы в data-URL и складываем в альбом.
  // Выбор файлов дополняет альбом, а не заменяет его: человек добирает кадры
  // по одному, из разных папок.
  document.getElementById('offerImageFile')?.addEventListener('change', async function () {
    const files = [...(this.files || [])];
    this.value = '';
    if (!files.length) return;
    const free = MAX_ALBUM - pageAlbum.length;
    if (free <= 0) {
      toast(`В альбом влезает ${MAX_ALBUM} картинок — уберите лишние`, 'warn');
      return;
    }
    if (files.length > free) {
      toast(`Добавим только ${free}: в альбоме максимум ${MAX_ALBUM} картинок`, 'warn');
    }
    let added = 0;
    for (const file of files.slice(0, free)) {
      const src = await readImageFile(file);
      if (!src || pageAlbum.includes(src)) continue;
      pageAlbum.push(src);
      added += 1;
    }
    if (added) {
      offerLog(
        added > 1 ? `Приложен альбом из ${pageAlbum.length} картинок` : 'Приложена картинка',
      );
    }
    syncAlbumToPreview();
  });

  // Удаление кадра из альбома.
  document.getElementById('offerAlbum')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-album-remove]');
    if (!button) return;
    pageAlbum.splice(Number(button.dataset.albumRemove), 1);
    syncAlbumToPreview();
  });

  // Вкладки конструктора и ИИ.
  document.querySelector('.offer-tabs')?.addEventListener('click', (e) => {
    const btn = e.target.closest('.offer-tab');
    if (btn) switchOfferTab(btn.dataset.offerTab);
  });

  // Чипы подстановок: делегирование, потому что список приходит из API.
  document.getElementById('placeholderChips')?.addEventListener('click', (e) => {
    const chip = e.target.closest('.ph-chip');
    if (!chip) return;
    if (chip.dataset.token) {
      insertPlaceholder(chip.dataset.token);
      showPlaceholderNote('');
    } else if (chip.dataset.unavailable) {
      const token = chip.querySelector('b')?.textContent || chip.textContent;
      showPlaceholderNote(`${token} — ${chip.dataset.unavailable}`);
    }
  });
  document.getElementById('aiOfferResult')?.addEventListener('click', (e) => {
    const button = e.target.closest('[data-ai-variant]');
    if (!button) return;
    const variant = aiPreviewVariants[Number(button.dataset.aiVariant)];
    if (!variant) return;
    button.parentElement
      .querySelectorAll('[data-ai-variant]')
      .forEach((item) => item.classList.toggle('active', item === button));
    renderTgPreview(variant.text, '', []);
  });
  window.addEventListener('mf:navigate', (event) => {
    if (event.detail?.target === 'offer' || event.detail?.target === 'mailing') loadOffers();
  });
}

/** Сбрасывает состояние модалки оффера перед открытием «нового». */
export function resetOfferModal(id) {
  if (id !== 'modalOffer') return;
  editingOfferId = null;
  document.getElementById('offerDesc').value = '';
  const h = document.querySelector('#modalOffer .modal-head h3');
  if (h) h.textContent = 'Новый оффер (сообщение)';
}
