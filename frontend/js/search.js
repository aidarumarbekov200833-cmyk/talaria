/** Страница поиска лидов: тренды, готовые ниши, свои слова.
 *
 *  Все данные здесь настоящие: темы приходят из трендов YouTube, ключевые слова
 *  собираются перебором на бэкенде, поиск каналов расходует квоту. Никаких
 *  имитаций прогресса — в логах пишем то, что реально произошло.
 *
 *  Шаги блокируются до выполнения предыдущего: показывать кнопку поиска, когда
 *  тем ещё нет, значит обещать работу, которая не запустится.
 */
import { api, esc } from './api.js';
import { toast } from './ui.js?v=tal32';
import { icon } from './icons.js';
import { refreshQuota } from './quota.js';
import { initRegionPicker } from './region-picker.js?v=talaria13';
import { logLine } from './logline.js?v=tal33';

/* Состояние страницы. Ключевые слова и темы храним здесь, а не в DOM:
   из разметки их пришлось бы парсить обратно, теряя source/evidence. */
let niches = []; // список ниш с бэкенда
let pickedNiches = []; // выбранные slug
let trendTopics = []; // TopicItem[] с последней генерации
let nicheKeywords = []; // строки
let customKeywords = [];
let activeTab = 1;
let verificationDatabaseId = 0;
let verificationPollTimer = null;

const SPIN =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spinning" style="width:16px;height:16px"><circle cx="12" cy="12" r="10" stroke-opacity=".3"/><path d="M12 2a10 10 0 0110 10"/></svg>';

/* ---------- логи ---------- */

/** Строка в терминал справа. Только факты: что запросили, что вернулось. */
export function log(text, kind = '') {
  logLine('parseLogs', text, kind);
}

/* ---------- вкладки и счётчики ---------- */

export function switchSearchTab(mode) {
  activeTab = mode;
  document
    .querySelectorAll('.search-tab')
    .forEach((t, i) => t.classList.toggle('active', i + 1 === mode));
  document
    .querySelectorAll('.search-tab-content')
    .forEach((c, i) => c.classList.toggle('active', i + 1 === mode));
}

export function searchClamp(input, low, high) {
  let v = parseInt(input.value, 10);
  if (!Number.isFinite(v)) v = low;
  input.value = Math.max(low, Math.min(high, v));
}

export function searchInc(id, high) {
  const el = document.getElementById(id);
  const step = high > 100 ? 10 : 1;
  el.value = Math.min(high, (parseInt(el.value, 10) || 0) + step);
  el.dispatchEvent(new Event('change'));
}

export function searchDec(id, low) {
  const el = document.getElementById(id);
  const high = parseInt(el.max, 10) || 1000;
  const step = high > 100 ? 10 : 1;
  el.value = Math.max(low, (parseInt(el.value, 10) || 0) - step);
  el.dispatchEvent(new Event('change'));
}

function num(id) {
  return parseInt(document.getElementById(id).value, 10) || 0;
}

function optionalNum(id) {
  const raw = (document.getElementById(id)?.value || '').trim();
  return raw === '' ? null : Math.max(0, Number.parseInt(raw, 10) || 0);
}

function optionalFloat(id) {
  const raw = (document.getElementById(id)?.value || '').trim().replace(',', '.');
  return raw === '' ? null : Math.max(0, Number.parseFloat(raw) || 0);
}

function collectLeadFilters() {
  const maxAge = optionalNum('leadMaxAge');
  return {
    min_subscribers: optionalNum('leadMinSubs') || 0,
    max_subscribers: optionalNum('leadMaxSubs'),
    min_views: optionalNum('leadMinViews') || 0,
    min_average_views: optionalNum('leadMinAverageViews') || 0,
    min_view_subscriber_ratio: optionalFloat('leadMinViewRatio') || 0,
    min_videos: optionalNum('leadMinVideos') || 0,
    max_channel_age_days: maxAge === null ? null : Math.max(1, maxAge),
    countries: selectedCodes('leadCountry'),
    include_hidden_subscribers: !!document.getElementById('leadIncludeHidden')?.checked,
  };
}

/** Коды из мультиселекта: и виджет, и нативный список читаются одинаково. */
function selectedCodes(id) {
  const select = document.getElementById(id);
  if (!select) return [];
  return [...select.selectedOptions].map((option) => option.value).filter(Boolean);
}

function applyCodes(id, codes) {
  const select = document.getElementById(id);
  if (!select) return;
  const wanted = new Set((codes || []).map((code) => String(code).toUpperCase()));
  [...select.options].forEach((option) => {
    option.selected = wanted.has(option.value);
  });
  select.dispatchEvent(new Event('change', { bubbles: true }));
}

/* ---------- фильтры: режим и запоминание ----------
   Пресеты убраны по просьбе владельца («название нового пресета — фигня, у
   каждого человека его прошлые настройки сохраняются всегда»). Фильтры и режим
   пишутся в localStorage при каждом изменении и восстанавливаются при входе. */

/** Рекомендованный набор: подобран так, чтобы в выдаче оставались живые каналы
 *  с контактами, а не заброшенные страницы. Страна не ограничивается — лиды
 *  ищутся во всех регионах сразу. */
const RECOMMENDED_FILTERS = {
  leadMinSubs: '1000',
  leadMaxSubs: '',
  leadMinViews: '0',
  leadMinAverageViews: '500',
  leadMinViewRatio: '1',
  leadMinVideos: '5',
  leadMaxAge: '',
};
const RECOMMENDED_NOTE =
  'от 1 000 подписчиков, 5+ видео, 500+ средних просмотров, вовлечённость от 1 %, все страны';
const MANUAL_NOTE = 'Поля открыты — меняйте что нужно, настройки сохраняются автоматически';
const FILTER_STATE_KEY = 'talaria.search-filters.v1';
const FILTER_FIELD_IDS = [...Object.keys(RECOMMENDED_FILTERS), 'leadIncludeHidden'];
let leadFilterMode = 'auto';

function writeFilterState() {
  const values = {};
  FILTER_FIELD_IDS.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    values[id] = el.type === 'checkbox' ? el.checked : el.value;
  });
  try {
    localStorage.setItem(
      FILTER_STATE_KEY,
      JSON.stringify({ mode: leadFilterMode, values, countries: selectedCodes('leadCountry') }),
    );
  } catch (_) {
    /* приватный режим браузера — настройки просто не запомнятся */
  }
}

function readFilterState() {
  try {
    const parsed = JSON.parse(localStorage.getItem(FILTER_STATE_KEY) || 'null');
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch (_) {
    return null;
  }
}

function applyRecommendedFilters() {
  Object.entries(RECOMMENDED_FILTERS).forEach(([id, value]) => {
    const el = document.getElementById(id);
    if (el) el.value = value;
  });
  const hidden = document.getElementById('leadIncludeHidden');
  if (hidden) hidden.checked = false;
  applyCodes('leadCountry', []);
}

function paintFilterMode() {
  const auto = leadFilterMode === 'auto';
  const panel = document.querySelector('.search-filter-panel');
  if (panel) panel.classList.toggle('is-auto', auto);
  const note = document.getElementById('leadModeNote');
  if (note) note.textContent = auto ? RECOMMENDED_NOTE : MANUAL_NOTE;
  [
    ['leadModeAuto', auto],
    ['leadModeManual', !auto],
  ].forEach(([id, on]) => {
    const button = document.getElementById(id);
    if (!button) return;
    button.classList.toggle('active', on);
    button.setAttribute('aria-pressed', String(on));
  });
  // В авто-режиме поля только для чтения: набор проверен, случайная правка
  // ломает выдачу молча. Кнопка «Вручную» открывает всё одним нажатием.
  document.querySelectorAll('.lead-filter-grid input').forEach((input) => {
    input.disabled = auto;
  });
  document.querySelectorAll('.filter-switches input').forEach((input) => {
    input.disabled = auto;
  });
  const country = document.querySelector('.region-picker[data-for="leadCountry"] button');
  if (country) country.disabled = auto;
}

/** Предзаполнение фильтров помощником. Раньше ИИ писал значения прямо в поля,
 *  но с v38 поля в авто-режиме заблокированы, а страна живёт в мультиселекте —
 *  «выставь фильтры» молча ничего не менял. Здесь один вход: режим переводится
 *  в ручной, значения проставляются, состояние сохраняется. */
export function applyAiSearchPrefill(action = {}) {
  setLeadFilterMode('manual');
  [
    ['leadMinSubs', action.min_subs],
    ['leadMaxSubs', action.max_subs],
  ].forEach(([id, value]) => {
    if (value === undefined || value === null) return;
    const el = document.getElementById(id);
    if (el) el.value = String(value);
  });
  if (typeof action.include_hidden === 'boolean') {
    const hidden = document.getElementById('leadIncludeHidden');
    if (hidden) hidden.checked = action.include_hidden;
  }
  const country = String(action.country || '').trim();
  if (country) applyCodes('leadCountry', [country]);
  writeFilterState();
}

export function setLeadFilterMode(mode) {
  leadFilterMode = mode === 'manual' ? 'manual' : 'auto';
  if (leadFilterMode === 'auto') applyRecommendedFilters();
  paintFilterMode();
  writeFilterState();
}

function restoreFilterState() {
  const saved = readFilterState();
  leadFilterMode = saved?.mode === 'manual' ? 'manual' : 'auto';
  if (leadFilterMode === 'auto' || !saved) {
    applyRecommendedFilters();
  } else {
    Object.entries(saved.values || {}).forEach(([id, value]) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (el.type === 'checkbox') el.checked = !!value;
      else el.value = value == null ? '' : String(value);
    });
    applyCodes('leadCountry', saved.countries || []);
  }
  paintFilterMode();
  // Любая правка сразу уходит в память: отдельной кнопки «сохранить» нет.
  document
    .querySelectorAll('.lead-filter-grid input, .filter-switches input, #leadCountry')
    .forEach((el) => el.addEventListener('change', writeFilterState));
}

export function resetLeadFilters() {
  applyRecommendedFilters();
  leadFilterMode = 'auto';
  paintFilterMode();
  writeFilterState();
  toast('Вернули рекомендованные настройки', 'info');
}

function setStep(id, state) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('locked', 'active', 'done');
  el.classList.add(state);
}

function showErr(id, message) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!message) {
    el.style.display = 'none';
    return;
  }
  el.textContent = message;
  el.style.display = 'block';
}

function busy(btnId, on, label) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  if (on) {
    btn.dataset.html = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = SPIN + ' ' + label;
  } else {
    btn.disabled = false;
    if (btn.dataset.html) btn.innerHTML = btn.dataset.html;
  }
}

/* ---------- список проверки ---------- */

/** Отрисовать элементы на проверку. Источник показываем у каждой темы:
    пользователь должен видеть, из какого видео она взялась. */
function renderReview(containerId, items, onChange) {
  const box = document.getElementById(containerId);
  box.innerHTML = '';
  items.forEach((item, idx) => {
    const row = document.createElement('div');
    row.className = 'review-item';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = true;
    cb.dataset.idx = String(idx);
    cb.addEventListener('change', () => {
      row.classList.toggle('dropped', !cb.checked);
      onChange();
    });
    const text = document.createElement('div');
    text.className = 'review-text';
    text.textContent = item.text;
    row.append(cb, text);
    if (item.note) {
      const src = document.createElement('span');
      src.className = 'review-src';
      src.textContent = item.note;
      src.title = item.title || item.note;
      row.appendChild(src);
    }
    box.appendChild(row);
  });
  onChange();
}

function checkedIndexes(containerId) {
  return Array.from(document.querySelectorAll(`#${containerId} input[type=checkbox]`))
    .filter((cb) => cb.checked)
    .map((cb) => parseInt(cb.dataset.idx, 10));
}

export function searchSelectAll(containerId, on) {
  document.querySelectorAll(`#${containerId} input[type=checkbox]`).forEach((cb) => {
    cb.checked = on;
    cb.dispatchEvent(new Event('change'));
  });
}

/* ---------- оценка стоимости ---------- */

/** Спросить бэкенд, сколько единиц квоты будет стоить поиск.
 *
 *  Цена берётся из ответа /api/search/estimate — своих чисел мы не считаем,
 *  чтобы показанное совпадало с тем, что реально спишется.
 */
async function showCost(boxId, keywords, limit) {
  const box = document.getElementById(boxId);
  box.style.display = 'none';
  if (!keywords.length) return null;
  try {
    const est = await api('POST', '/api/search/estimate', { keywords, limit });
    box.className = 'cost-note' + (est.enough ? '' : ' over');
    // Значение считается на одно ключевое слово, поэтому показываем арифметику
    // целиком: иначе «50» в поле и 2500 найденных каналов выглядят как ошибка.
    const total = est.total_channels || keywords.length * limit;
    const math = `${keywords.length} кл. слов × ${limit} = до ${total} каналов. `;
    box.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01M12 12v4"/></svg><div>${esc(math)}${esc(est.note)}</div>`;
    box.style.display = 'flex';
    return est;
  } catch (e) {
    // Нет ключа — сообщение бэкенда точнее любого нашего.
    box.className = 'cost-note over';
    box.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/></svg><div>${esc(e.message)}</div>`;
    box.style.display = 'flex';
    return null;
  }
}

/* ---------- ВКЛАДКА 1: ТРЕНДЫ ---------- */

export async function generateTrends() {
  const count = num('trendsCount');
  const regions = selectedCodes('trendsRegion');
  if (!regions.length) {
    showErr('trendsStep1Err', 'Выберите хотя бы один регион');
    return;
  }
  showErr('trendsStep1Err', '');
  busy('trendsGenBtn', true, 'Запрос к YouTube…');
  log(`Запрос трендов: регионы ${regions.join(', ')}, тем ${count}`, 'info');
  try {
    const run = await api('POST', '/api/topics', { mode: 'trends', count, regions });
    trendTopics = run.topics || [];
    if (!trendTopics.length) {
      showErr('trendsStep1Err', 'YouTube не вернул подходящих тем. Попробуйте другой регион.');
      log('Тем не найдено', 'warn');
      return;
    }
    log(`Получено тем: ${trendTopics.length}, потрачено единиц: ${run.units_spent}`, 'info');
    renderReview(
      'trendsReview',
      trendTopics.map((t) => ({
        text: t.topic,
        note: t.channel || t.source,
        title: t.evidence,
      })),
      updateTrendsSelection,
    );
    setStep('trendsStep1', 'done');
    setStep('trendsStep2', 'active');
    setStep('trendsStep3', 'active');
    if (trendTopics.length < count) {
      log(`Запрошено было ${count} — больше подходящих не нашлось`, 'warn');
    }
    refreshQuota();
  } catch (e) {
    showErr('trendsStep1Err', e.message);
    log('Ошибка: ' + e.message, 'warn');
  } finally {
    busy('trendsGenBtn', false);
  }
}

function updateTrendsSelection() {
  const picked = checkedIndexes('trendsReview');
  document.getElementById('trendsSelected').textContent = picked.length;
  document.getElementById('trendsTotal').textContent = trendTopics.length;
  const words = picked.map((i) => trendTopics[i].topic);
  showCost('trendsCost', words, num('trendsLimit'));
}

export async function runTrendsSearch() {
  const picked = checkedIndexes('trendsReview').map((i) => trendTopics[i].topic);
  if (!picked.length) {
    toast('Не выбрано ни одной темы', 'warn');
    return;
  }
  await doSearch(
    {
      keywords: picked,
      limit: num('trendsLimit'),
      database_name: document.getElementById('trendsDbName').value.trim(),
      mode: 'trends',
      // Поиск каналов YouTube принимает один регион: берём первый выбранный.
      region: selectedCodes('trendsRegion')[0] || '',
    },
    'trendsSearchBtn',
    'trendsStep3Err',
  );
}

/* ---------- ВКЛАДКА 2: ГОТОВЫЕ НИШИ ---------- */

async function loadNiches() {
  const grid = document.getElementById('nicheGrid');
  if (!grid) return;
  try {
    const data = await api('GET', '/api/niches');
    niches = data.niches || [];
  } catch (e) {
    grid.innerHTML = `<div class="table-empty">Не удалось загрузить ниши: ${esc(e.message)}</div>`;
    return;
  }
  grid.innerHTML = '';
  niches.forEach((n) => {
    const tile = document.createElement('div');
    tile.className = 'niche-tile';
    tile.dataset.slug = n.slug;
    tile.innerHTML = `
      <div class="niche-check"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M20 6L9 17l-5-5"/></svg></div>
      <div class="niche-ic">${icon(n.icon)}</div>
      <div class="niche-name">${esc(n.title)}</div>`;
    tile.addEventListener('click', () => toggleNiche(n.slug, tile));
    grid.appendChild(tile);
  });
}

function toggleNiche(slug, tile) {
  const at = pickedNiches.indexOf(slug);
  if (at === -1) pickedNiches.push(slug);
  else pickedNiches.splice(at, 1);
  tile.classList.toggle('selected', at === -1);
  document.getElementById('nichesPicked').textContent = pickedNiches.length;
  document.getElementById('nichesGenKwBtn').disabled = pickedNiches.length === 0;
}

export async function generateNicheKeywords() {
  const count = num('nicheKwCount');
  busy('nichesGenKwBtn', true, 'Сборка…');
  try {
    const data = await api('POST', '/api/keywords', { niches: pickedNiches, count });
    nicheKeywords = data.keywords || [];
    document.getElementById('kwSourceNote').textContent = data.source_note || '';
    renderReview(
      'nicheKwReview',
      nicheKeywords.map((k) => ({ text: k })),
      updateNicheSelection,
    );
    setStep('nichesStep1', 'done');
    setStep('nichesStep2', 'active');
    setStep('nichesStep3', 'active');
    log(`Собрано ключевых слов: ${nicheKeywords.length} (квота не тратится)`, 'info');
  } catch (e) {
    toast(e.message, 'warn');
    log('Ошибка сборки слов: ' + e.message, 'warn');
  } finally {
    busy('nichesGenKwBtn', false);
  }
}

export function addNicheExtraKw() {
  const inp = document.getElementById('nicheExtraKw');
  const words = parseKeywordList(inp.value);
  if (!words.length) return;
  let added = 0;
  words.forEach((word) => {
    if (nicheKeywords.some((k) => k.toLowerCase() === word.toLowerCase())) return;
    nicheKeywords.push(word);
    added += 1;
  });
  if (added) {
    renderReview(
      'nicheKwReview',
      nicheKeywords.map((k) => ({ text: k })),
      updateNicheSelection,
    );
  }
  if (added > 1) toast(`Добавлено слов: ${added}`, 'success');
  inp.value = '';
}

function updateNicheSelection() {
  const picked = checkedIndexes('nicheKwReview');
  document.getElementById('nicheKwSelected').textContent = picked.length;
  showCost(
    'nicheCost',
    picked.map((i) => nicheKeywords[i]),
    num('nicheLimit'),
  );
}

export async function runNicheSearch() {
  const picked = checkedIndexes('nicheKwReview').map((i) => nicheKeywords[i]);
  if (!picked.length) {
    toast('Не выбрано ни одного ключевого слова', 'warn');
    return;
  }
  await doSearch(
    {
      keywords: picked,
      limit: num('nicheLimit'),
      database_name: document.getElementById('nicheDbName').value.trim(),
      mode: 'niches',
    },
    'nicheSearchBtn',
    'nicheStep3Err',
  );
}

/* ---------- ВКЛАДКА 3: СВОИ СЛОВА ---------- */

/** Разобрать строку в список слов.
 *
 *  Владелец вставляет готовый список: «монтаж, обзоры игр, рецепты» или просто
 *  слова через пробел. Раньше вся строка становилась одним ключевым словом, и
 *  YouTube искал фразу целиком. Запятая и перевод строки — главный разделитель;
 *  если их нет, делим по пробелам.
 */
function parseKeywordList(raw) {
  const text = String(raw || '').trim();
  if (!text) return [];
  const parts = /[,;\n\r\t]/.test(text) ? text.split(/[,;\n\r\t]+/) : text.split(/\s+/);
  const seen = new Set();
  const words = [];
  parts.forEach((part) => {
    const word = part
      .trim()
      .replace(/^[-•·]+\s*/, '')
      .slice(0, 200);
    if (!word) return;
    const key = word.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    words.push(word);
  });
  return words;
}

export function addCustomKeyword() {
  const inp = document.getElementById('customKwInput');
  const words = parseKeywordList(inp.value);
  if (!words.length) return;
  let added = 0;
  words.forEach((word) => {
    if (customKeywords.some((k) => k.toLowerCase() === word.toLowerCase())) return;
    customKeywords.push(word);
    added += 1;
  });
  if (added) renderCustomChips();
  if (added > 1) toast(`Добавлено слов: ${added}`, 'success');
  inp.value = '';
}

export function removeCustomKeyword(kw) {
  customKeywords = customKeywords.filter((k) => k !== kw);
  renderCustomChips();
}

function renderCustomChips() {
  const wrap = document.getElementById('customKeywordsChips');
  wrap.innerHTML = '';
  customKeywords.forEach((kw) => {
    const chip = document.createElement('div');
    chip.className = 'chip active';
    chip.textContent = kw + ' ';
    const x = document.createElement('span');
    x.className = 'chip-remove';
    x.textContent = '✕';
    // Слушатель, а не onclick со строкой: в слове могут быть кавычки.
    x.addEventListener('click', (ev) => {
      ev.stopPropagation();
      removeCustomKeyword(kw);
    });
    chip.appendChild(x);
    wrap.appendChild(chip);
  });
  setStep('customStep2', customKeywords.length ? 'active' : 'locked');
  if (customKeywords.length) showCost('customCost', customKeywords, num('customLimit'));
}

export async function runCustomSearch() {
  if (!customKeywords.length) {
    toast('Добавьте хотя бы одно ключевое слово', 'warn');
    return;
  }
  await doSearch(
    {
      keywords: customKeywords,
      limit: num('customLimit'),
      database_name: document.getElementById('customDbName').value.trim(),
      mode: 'custom',
    },
    'customSearchBtn',
    'customStep2Err',
  );
}

/* ---------- общий запуск поиска ---------- */

/** Кнопка запуска по режиму прогона — чтобы после F5 подсветить нужную. */
const SEARCH_BTN_BY_MODE = {
  trends: 'trendsSearchBtn',
  niches: 'nicheSearchBtn',
  custom: 'customSearchBtn',
};

/** Дождаться завершения прогона поиска, транслируя прогресс в терминал.
 *
 *  Прогон живёт на сервере и переживает и закрытую вкладку, и рестарт —
 *  здесь только опрос состояния. В лог идут факты из ответа, ничего выдуманного.
 */
async function trackSearchJob(job) {
  let lastKeyword = '';
  const paint = (j) => {
    if (j.current_keyword && j.current_keyword !== lastKeyword) {
      lastKeyword = j.current_keyword;
      const step = Math.min((j.keywords_done || 0) + 1, j.keywords_total || 1);
      const found = j.found_count ? ` · найдено ${j.found_count}` : '';
      log(`Слово ${step}/${j.keywords_total}: «${j.current_keyword}»${found}`, 'info');
    }
  };
  paint(job);
  let current = job;
  while (current.status === 'queued' || current.status === 'running') {
    await new Promise((resolve) => setTimeout(resolve, 1600));
    try {
      current = await api('GET', '/api/search/jobs/current');
    } catch {
      continue; // сеть мигнула — следующий опрос покажет реальное состояние
    }
    if (current.id !== job.id) break; // это уже другой прогон
    paint(current);
  }
  return current;
}

/** Общий финал успешного прогона: журнал, списки баз, автопроверка. */
async function handleSearchSuccess(finished) {
  const db = finished.database;
  log(`Готово: база «${db.name}», каналов ${db.leads_count}, единиц ${db.units_spent}`, 'info');
  if (finished.stop_reason) log(`Остановка: ${finished.stop_reason}`, 'warn');
  toast(`Найдено каналов: ${db.leads_count}. База «${db.name}» создана.`, 'success');
  refreshQuota();
  // Списки баз и получателей обновляем сразу — иначе новая база не видна.
  if (window.loadDatabases) window.loadDatabases();
  if (window.loadCampaignBases) window.loadCampaignBases();
  await loadVerificationDatabases(db.id);
  // Проверку Telegram сервер запускает сам сразу после поиска; этот вызов
  // только подхватывает её состояние (и остаётся запасным путём, если на
  // сервере проверять было нечем).
  await autoStartVerification(db.id);
}

/** Запустить поиск каналов и сохранить базу.
 *
 *  Запрос создаёт прогон на сервере и сразу возвращается; дальше — опрос
 *  прогресса. Рестарт сервера прогон не теряет: он продолжится сам.
 */
async function doSearch(payload, btnId, errId) {
  Object.assign(payload, collectLeadFilters());
  showErr(errId, '');
  busy(btnId, true, 'Идёт поиск…');
  log(`Поиск каналов: слов ${payload.keywords.length}, лимит ${payload.limit}`, 'info');
  try {
    const started = await api('POST', '/api/search', payload);
    const finished = await trackSearchJob(started);
    if (finished.status !== 'completed' || !finished.database) {
      throw new Error(finished.error || 'Поиск не выполнен');
    }
    if (finished.database.leads_count < payload.limit) {
      log(`Запрошено было ${payload.limit} — найдено меньше`, 'warn');
    }
    await handleSearchSuccess(finished);
  } catch (e) {
    showErr(errId, e.message);
    log('Поиск не выполнен: ' + e.message, 'warn');
    // Отсутствие ключа — самая частая причина; выносим в уведомление,
    // потому что исправлять её надо в другом разделе.
    if (/ключ YouTube API/i.test(e.message)) toast(e.message, 'warn');
  } finally {
    busy(btnId, false);
  }
}

/** Подхватить прогон, который шёл до перезагрузки страницы или рестарта
 *  сервера: терминал снова показывает прогресс, кнопка занята до конца. */
async function resumeActiveSearchJob() {
  let job;
  try {
    job = await api('GET', '/api/search/jobs/current');
  } catch {
    return; // поиск ещё не запускался
  }
  if (!job || !['queued', 'running'].includes(job.status)) return;
  const btnId = SEARCH_BTN_BY_MODE[job.mode] || 'customSearchBtn';
  log('Поиск продолжается на сервере — подключаемся к прогрессу', 'info');
  busy(btnId, true, 'Идёт поиск…');
  try {
    const finished = await trackSearchJob(job);
    if (finished.status !== 'completed' || !finished.database) {
      throw new Error(finished.error || 'Поиск не выполнен');
    }
    await handleSearchSuccess(finished);
  } catch (e) {
    log('Поиск не выполнен: ' + e.message, 'warn');
  } finally {
    busy(btnId, false);
  }
}

function pipelineLabel(status) {
  return (
    {
      youtube_ready: 'База готова к проверке',
      verifying: 'Проверка идёт',
      ready: 'Готово к рассылке',
      partial: 'Проверено, нужны контакты',
    }[status] || status
  );
}

function stopVerificationPolling() {
  if (verificationPollTimer) clearInterval(verificationPollTimer);
  verificationPollTimer = null;
}

// Выход из системы — опрос проверки базы больше не имеет права стучать в API.
window.addEventListener('talaria:logout', stopVerificationPolling);

function startVerificationPolling() {
  if (verificationPollTimer) return;
  verificationPollTimer = setInterval(() => {
    loadVerificationSummary().catch(() => {});
  }, 1800);
}

function verificationJobStatus(job) {
  return (
    {
      queued: 'Запускаем проверку',
      running: 'Проверяем всю базу',
      cancel_requested: 'Останавливаем',
      cancelled: 'Проверка остановлена',
      completed: 'Проверка завершена',
      error: 'Проверка остановлена ошибкой',
    }[job?.status] || 'Готово к запуску'
  );
}

function paintVerificationJob(summary) {
  const job = summary.job || null;
  const active = ['queued', 'running', 'cancel_requested'].includes(job?.status);
  const current = document.getElementById('verificationCurrent');
  const results = document.getElementById('verificationResults');
  const start = document.getElementById('verificationRunBtn');
  const cancel = document.getElementById('verificationCancelBtn');

  if (current) {
    current.classList.toggle('is-running', active);
    current.classList.toggle('is-error', job?.status === 'error');
    current.innerHTML = `
      <span class="verify-live-dot"></span>
      <div><b>${esc(verificationJobStatus(job))}</b><small>${esc(
        active
          ? job.current_title ||
              `Обработано ${Number(job.processed || 0).toLocaleString('ru-RU')} из ${Number(job.total || 0).toLocaleString('ru-RU')}`
          : job?.last_result || 'Нажмите одну кнопку — остальное Talaria сделает сам',
      )}</small></div>`;
  }

  if (cancel) cancel.hidden = !active || job?.status === 'cancel_requested';
  if (start) {
    start.disabled = active || !summary.accounts_connected || (!summary.pending && !summary.errors);
    start.dataset.retryErrors = String(!summary.pending && summary.errors > 0);
    start.textContent = active
      ? `Проверяется ${Number(job?.processed || 0).toLocaleString('ru-RU')} / ${Number(job?.total || 0).toLocaleString('ru-RU')}`
      : summary.pending
        ? `Проверить всю базу (${Number(summary.pending).toLocaleString('ru-RU')})`
        : summary.errors
          ? `Повторить ошибки (${Number(summary.errors).toLocaleString('ru-RU')})`
          : 'Проверка завершена';
  }

  if (!summary.accounts_connected) {
    if (results)
      results.innerHTML =
        '<div class="notice warn">Подключите хотя бы один Telegram-аккаунт в настройках — после этого проверка всей базы запустится одной кнопкой.</div>';
  } else if (job && results) {
    results.innerHTML = `
      <div class="verify-result verify-job-result">
        <div><b>${esc(verificationJobStatus(job))}</b><div class="field-hint">${esc(job.current_title || job.last_result || 'Очередь готова')}</div></div>
        <div class="${job.errors ? 'bad' : 'ok'}">${Number(job.verified || 0).toLocaleString('ru-RU')} USER ✓</div>
        <div class="field-hint">Без получателя: ${Number(job.unresolved || 0).toLocaleString('ru-RU')} · ошибок: ${Number(job.errors || 0).toLocaleString('ru-RU')}</div>
      </div>`;
  }

  if (active) startVerificationPolling();
  else stopVerificationPolling();
}

async function loadVerificationDatabases(preselect = 0) {
  const select = document.getElementById('verificationDatabase');
  if (!select) return;
  try {
    const data = await api('GET', '/api/leads/databases');
    const rows = data.databases || [];
    select.innerHTML =
      '<option value="">Выберите базу…</option>' +
      rows
        .map(
          (row) =>
            `<option value="${Number(row.id)}">${esc(row.name)} · ${Number(row.leads_count || 0).toLocaleString('ru-RU')} каналов</option>`,
        )
        .join('');
    if (preselect) select.value = String(preselect);
    if (!select.value && rows.length) select.value = String(rows[0].id);
    verificationDatabaseId = Number(select.value || 0);
    if (verificationDatabaseId) await loadVerificationSummary();
  } catch (e) {
    document.getElementById('verificationResults').innerHTML =
      `<div class="dd-empty">${esc(e.message)}</div>`;
  }
}

async function loadVerificationSummary() {
  const id = Number(
    document.getElementById('verificationDatabase')?.value || verificationDatabaseId || 0,
  );
  if (!id) return;
  verificationDatabaseId = id;
  const summary = await api('GET', `/api/leads/databases/${id}/telegram-summary`);
  const badge = document.getElementById('verificationStatus');
  badge.textContent = pipelineLabel(summary.pipeline_status);
  badge.className = `pipeline-badge ${summary.pipeline_status === 'ready' ? 'ready' : summary.pipeline_status === 'verifying' ? 'running' : 'partial'}`;
  document.getElementById('verificationProgressBar').style.width =
    `${Math.max(0, Math.min(100, summary.progress || 0))}%`;
  document.getElementById('verificationKpis').innerHTML = `
    <span><b>${Number(summary.total || 0).toLocaleString('ru-RU')}</b>каналов YouTube</span>
    <span><b>${Number(summary.verified || 0).toLocaleString('ru-RU')}</b>личных user</span>
    <span><b>${Number(summary.pending || 0).toLocaleString('ru-RU')}</b>ожидает</span>
    <span><b>${Number((summary.missing || 0) + (summary.channels || 0) + (summary.bots || 0) + (summary.errors || 0)).toLocaleString('ru-RU')}</b>без получателя</span>`;
  paintVerificationJob(summary);
  return summary;
}

/** Запустить проверку сразу после поиска.
 *
 *  Тихо: если аккаунтов Telegram нет или проверять нечего — просто ничего не
 *  делаем, лишнее предупреждение сразу после успешного поиска только пугает.
 */
async function autoStartVerification(databaseId) {
  if (!databaseId) return;
  try {
    const summary = await api('GET', `/api/leads/databases/${databaseId}/telegram-summary`);
    const active = ['queued', 'running', 'cancel_requested'].includes(summary?.job?.status);
    if (active || !summary?.accounts_connected || !summary?.pending) return;
    const job = await api('POST', `/api/leads/databases/${databaseId}/verification/start`, {
      retry_errors: false,
    });
    log(`Telegram: проверка запущена автоматически, каналов ${job.total}`, 'info');
    toast('Проверка базы запущена автоматически', 'success');
    startVerificationPolling();
    await loadVerificationSummary();
  } catch (e) {
    log('Автопроверка не запустилась: ' + e.message, 'warn');
  }
}

export async function runTelegramVerification() {
  const id = Number(document.getElementById('verificationDatabase')?.value || 0);
  if (!id) {
    toast('Выберите базу каналов', 'warn');
    return;
  }
  const button = document.getElementById('verificationRunBtn');
  busy(button.id, true, 'Запускаем…');
  try {
    const retryErrors = button?.dataset.retryErrors === 'true';
    const job = await api('POST', `/api/leads/databases/${id}/verification/start`, {
      retry_errors: retryErrors,
    });
    log(`Telegram: запущена полная проверка ${job.total} каналов`, 'info');
    toast('Верификация запущена и продолжится в фоне', 'success');
    startVerificationPolling();
    await loadVerificationSummary();
    if (window.loadDatabases) window.loadDatabases();
  } catch (e) {
    toast(e.message, 'warn');
    log('Telegram-верификация: ' + e.message, 'warn');
  } finally {
    busy(button.id, false);
    await loadVerificationSummary().catch(() => {});
  }
}

export async function cancelTelegramVerification() {
  const id = Number(
    document.getElementById('verificationDatabase')?.value || verificationDatabaseId || 0,
  );
  if (!id) return;
  const button = document.getElementById('verificationCancelBtn');
  if (button) button.disabled = true;
  try {
    await api('POST', `/api/leads/databases/${id}/verification/cancel`, {});
    toast('Остановим после текущего канала', 'info');
    await loadVerificationSummary();
  } catch (e) {
    toast(e.message, 'warn');
  } finally {
    if (button) button.disabled = false;
  }
}

export function initSearch() {
  const grid = document.getElementById('nicheGrid');
  if (!grid) return;
  initRegionPicker('trendsRegion', { multi: true, emptyLabel: 'Выберите регионы' });
  initRegionPicker('leadCountry', { multi: true, emptyLabel: 'Любая страна' });
  loadNiches();
  restoreFilterState();
  loadVerificationDatabases();
  // Поиск, запущенный до перезагрузки, продолжается на сервере — подхватываем.
  resumeActiveSearchJob();
  document.getElementById('verificationDatabase')?.addEventListener('change', async (event) => {
    verificationDatabaseId = Number(event.target.value || 0);
    if (verificationDatabaseId) await loadVerificationSummary();
  });
  // Пересчёт стоимости при смене лимита: цена зависит от числа каналов.
  [
    ['trendsLimit', updateTrendsSelection],
    ['nicheLimit', updateNicheSelection],
  ].forEach(([id, fn]) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', fn);
  });
  const customLimit = document.getElementById('customLimit');
  if (customLimit) {
    customLimit.addEventListener('change', () => {
      if (customKeywords.length) showCost('customCost', customKeywords, num('customLimit'));
    });
  }
}
