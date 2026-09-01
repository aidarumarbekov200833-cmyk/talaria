/** Авторизация через официальный виджет Telegram Login.
 *
 *  Как это работает:
 *  1. Спрашиваем у бэкенда /api/config — есть ли настроенный бот.
 *  2. Если есть — вставляем официальный скрипт telegram-widget.js. Telegram сам
 *     рисует кнопку внутри своего <iframe>: её вид задан Telegram и не
 *     стилизуется нами (это требование платформы, а не наше ограничение).
 *  3. Пользователь жмёт кнопку, Telegram возвращает подписанные параметры на
 *     эту же страницу через data-auth-url.
 *  4. Забираем параметры из URL и отправляем их на /api/auth/telegram, где
 *     проверяется
 *     подпись HMAC-SHA256. Клиенту верить нельзя — решает только бэкенд.
 *
 *  Важно про домен: виджет отказывается рисоваться, если домен страницы не
 *  указан боту через @BotFather -> /setdomain. Молча ничего не появится,
 *  поэтому ниже есть таймаут и подсказка пользователю.
 */
import {
  api,
  setToken,
  clearToken,
  getToken,
  setUnauthorizedHandler,
  resetUnauthorized,
} from './api.js';
import { toast } from './ui.js?v=tal32';
import { goTo, restoreLastSection } from './nav.js?v=zephyr4';
import { maybeShowOnboarding } from './onboarding.js?v=zephyr4';
import { animateCounters, loadNotifications } from './dashboard.js?v=tal33';
import { loadDatabases } from './databases.js?v=note1';
import { loadOffers, loadPlaceholders, loadAiOfferSettings } from './offers.js?v=aioffer1';
import {
  loadCampaigns,
  loadCampaignBases,
  fillTestSendOffers,
  refreshAiAvailability,
} from './campaigns.js?v=zephyr4';
import { loadSettings, loadKeys, loadWarmup } from './settings.js?v=sess1';
import { applyAdminVisibility, loadAdmin } from './admin.js?v=talaria15';
// Тот же URL модуля, что и в main.js: разные query-string создают два
// независимых state/слушателя, и тариф рисует одна копия, а клики ждёт другая.
import { loadSubscription, loadPublicPlans } from './plans.js?v=phone3';
import { connectLive, disconnectLive } from './live.js?v=tal32';
import { initTheme } from './theme.js?v=zephyr3';
import { renderText } from './shared/dom.js';
import { bindDialogOpeners } from './shared/dialogs.js';

const WIDGET_SRC = 'https://telegram.org/js/telegram-widget.js?22';
const OAUTH_URL = 'https://oauth.telegram.org/auth';
const WIDGET_TIMEOUT_MS = 15000;
const TELEGRAM_AUTH_FIELDS = [
  'id',
  'first_name',
  'last_name',
  'username',
  'photo_url',
  'auth_date',
  'hash',
];

function slots() {
  // Telegram's loader relies on the currently executing script. Mounting two
  // copies in parallel can race and leave one slot empty, so keep one official
  // widget in the dialog and let every landing CTA open that dialog.
  return ['tg-widget-dialog-container'].map((id) => document.getElementById(id)).filter(Boolean);
}

function setHint(text, isError) {
  ['loginHint', 'dialogLoginHint'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    renderText(el, text);
    el.classList.toggle('error', !!isError);
    el.style.display = text ? 'block' : 'none';
  });
}

function widgetReturnUrl() {
  const url = new URL(window.location.href);
  TELEGRAM_AUTH_FIELDS.forEach((field) => url.searchParams.delete(field));
  url.hash = '';
  return url.toString();
}

/** Прямая ссылка на страницу входа Telegram — тот же redirect-flow, что делает
 * официальный виджет, только без его iframe.
 *
 * Виджет остаётся основным путём, но его iframe появляется не у всех: его режут
 * блокировщики, корпоративные фильтры и провайдеры. Тогда наша кнопка была
 * мёртвой — курсор даже не менялся, потому что кликать было не по чему.
 */
function directTelegramLoginUrl(botId) {
  if (!botId) return null;
  const url = new URL(OAUTH_URL);
  url.searchParams.set('bot_id', String(botId));
  url.searchParams.set('origin', window.location.origin);
  url.searchParams.set('request_access', 'write');
  url.searchParams.set('return_to', widgetReturnUrl());
  return url.toString();
}

/** Делает нашу кнопку самостоятельной: клик мимо iframe уводит на Telegram сам.
 *
 * Если iframe виджета лежит поверх и работает, клик до этого обработчика не
 * доходит — Telegram обрабатывает его сам, поведение не меняется.
 */
function bindDirectTelegramLogin(botId) {
  const cta = document.getElementById('tgLoginCta');
  const url = directTelegramLoginUrl(botId);
  if (!cta || !url) return;
  cta.classList.add('is-direct');
  cta.setAttribute('role', 'button');
  cta.setAttribute('tabindex', '0');
  cta.setAttribute('aria-label', 'Продолжить через Telegram');
  if (cta.dataset.directLoginBound === String(botId)) return;
  cta.dataset.directLoginBound = String(botId);
  const go = () => {
    window.location.href = directTelegramLoginUrl(botId);
  };
  cta.addEventListener('click', go);
  cta.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    go();
  });
}

function mountTelegramWidgetInto(host, botUsername) {
  if (host.dataset.telegramWidgetMounted === botUsername) return;
  host.dataset.telegramWidgetMounted = botUsername;
  const spinner = document.createElement('div');
  spinner.className = 'login-spinner';
  spinner.setAttribute('aria-hidden', 'true');
  host.replaceChildren(spinner);

  const widgetObserver = new MutationObserver(() => {
    if (!host.querySelector('iframe')) return;
    const spinner = host.querySelector('.login-spinner');
    if (spinner) spinner.remove();
    setHint('');
    // Кнопку Telegram подменяем своей только после того, как iframe реально
    // появился: без него клик было бы некуда девать, и человек стучал бы в
    // мёртвую кнопку.
    document.getElementById('tgLoginCta')?.classList.add('is-ready');
    widgetObserver.disconnect();
  });
  widgetObserver.observe(host, { childList: true, subtree: true });

  const s = document.createElement('script');
  s.async = true;
  s.src = WIDGET_SRC;
  s.setAttribute('data-telegram-login', botUsername);
  s.setAttribute('data-size', 'large');
  s.setAttribute('data-radius', '12');
  s.setAttribute('data-userpic', 'true');
  // data-onauth официальный widget компилирует через eval. Строгая production
  // CSP запрещает unsafe-eval, поэтому используем официальный redirect-flow:
  // Telegram вернёт подписанные поля на эту же страницу, а initAuth их заберёт.
  s.setAttribute('data-auth-url', widgetReturnUrl());
  // Тёмный режим виджета (data-dark) не используется сознательно: Telegram
  // рисует его iframe с непрозрачным чёрным фоном, и на странице входа
  // появлялся «чёрный ящик» вокруг кнопки. Обычная кнопка прозрачная.

  s.onload = () => {
    const spinner = host.querySelector('.login-spinner');
    if (spinner) spinner.remove();
  };

  s.onerror = () => {
    widgetObserver.disconnect();
    delete host.dataset.telegramWidgetMounted;
    host.replaceChildren();
    // Прямая ссылка на oauth.telegram.org уже привязана к кнопке — вход
    // работает и без виджета, пугать ошибкой незачем.
    if (document.getElementById('tgLoginCta')?.classList.contains('is-direct')) return;
    setHint(
      'Не удалось загрузить кнопку Telegram. Проверьте соединение и блокировщики рекламы.',
      true,
    );
  };

  host.appendChild(s);

  // Если iframe не появился вообще, скрипт не смог инициализироваться. Ошибка
  // домена приходит уже внутри iframe, поэтому не выдаём её за единственную
  // возможную причину (CSP/блокировщик/сеть встречаются не реже).
  setTimeout(() => {
    if (host.querySelector('iframe')) return;
    const spinner = host.querySelector('.login-spinner');
    if (spinner) spinner.remove();
    if (document.getElementById('tgLoginCta')?.classList.contains('is-direct')) return;
    setHint(
      'Telegram Login не инициализировался. Проверьте загрузку telegram-widget.js, ' +
        'блокировщики контента и Content Security Policy.',
      true,
    );
  }, WIDGET_TIMEOUT_MS);
}

/** Вставляет официальную кнопку Telegram в hero-карточку и прямой login-dialog. */
function mountTelegramWidget(botUsername) {
  setHint('');
  slots().forEach((host) => mountTelegramWidgetInto(host, botUsername));
}

function closeTelegramLoginDialog() {
  const dialog = document.getElementById('telegramLoginDialog');
  if (dialog?.open) dialog.close();
}

/** Все основные CTA открывают вход сразу, а не прокручивают страницу к карточке.
 *
 * Кроме прямой привязки вешаем делегированный обработчик на документ: карточка
 * тарифа рисуется после загрузки данных, её кнопка «Начать бесплатно» попадает
 * в DOM позже этого вызова и без делегирования осталась бы мёртвой. Импортировать
 * auth.js из tariffs.js нельзя — получится цикл с разными query-string у модуля,
 * то есть два экземпляра auth.js с раздельным состоянием.
 */
let delegatedLoginBound = false;

function bindTelegramLoginTriggers() {
  const dialog = document.getElementById('telegramLoginDialog');
  if (!dialog) return null;
  if (!delegatedLoginBound) {
    delegatedLoginBound = true;
    document.addEventListener('click', (event) => {
      const trigger = event.target?.closest?.('[data-telegram-login-trigger]');
      // Элементы, привязанные напрямую, уже обработаны своим слушателем.
      if (!trigger || trigger.dataset.loginBound === '1') return;
      event.preventDefault();
      if (hasWarmSession()) {
        enterWithWarmSession();
        return;
      }
      dialog.showModal?.();
    });
  }
  // Живая сессия есть — кнопка «Войти» не должна открывать окно Telegram: это
  // выглядело бы как повторный вход там, где человек и так уже вошёл. Ловим
  // клик раньше открытия окна и просто входим.
  document.querySelectorAll('[data-telegram-login-trigger]').forEach((trigger) => {
    trigger.addEventListener(
      'click',
      (event) => {
        if (!hasWarmSession()) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        enterWithWarmSession();
      },
      true,
    );
  });
  const direct = document.querySelectorAll('[data-telegram-login-trigger]');
  direct.forEach((el) => {
    el.dataset.loginBound = '1';
  });
  return bindDialogOpeners(dialog, direct);
}

/** Если Telegram-бот не настроен, показываем ошибку конфигурации без фальшивого входа. */
function showUnavailableLogin() {
  ['demoLoginBtn', 'demoDialogLoginBtn'].forEach((id) => {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.style.display = 'none';
  });
  setHint('Вход временно недоступен: Telegram-бот не настроен на сервере.', true);
}
async function authenticateTelegram(user) {
  setHint('Проверяем подпись…');
  try {
    const data = await api('POST', '/api/auth/telegram', user);
    setToken(data.access_token);
    setHint('');
    enterApp(data.user);
    return { authenticated: true, error: '' };
  } catch (e) {
    const error = String(e && e.message ? e.message : 'Сервер отклонил вход');
    setHint(`Вход не выполнен: ${error}`, true);
    toast('Ошибка входа через Telegram', 'warn');
    return { authenticated: false, error };
  }
}

/**
 * Telegram redirect оставляет auth-поля в query string. Удаляем их из адресной
 * строки до сетевого запроса, чтобы подпись не осталась в history/скриншотах.
 */
async function consumeTelegramRedirect() {
  const params = new URLSearchParams(window.location.search);
  if (!params.has('id') || !params.has('auth_date') || !params.has('hash')) {
    return { found: false, authenticated: false, error: '' };
  }

  const payload = {};
  TELEGRAM_AUTH_FIELDS.forEach((field) => {
    const value = params.get(field);
    if (value !== null) payload[field] = value;
    params.delete(field);
  });

  const cleanUrl = `${window.location.pathname}${params.toString() ? `?${params}` : ''}`;
  window.history.replaceState({}, document.title, cleanUrl);
  const attempt = await authenticateTelegram(payload);
  return { found: true, ...attempt };
}

/** Сессия закончилась: любой запрос вернул 401.
 *
 *  Отдельная функция, а не logout(): выход дёргает /api/auth/logout, а с мёртвым
 *  токеном этот запрос снова вернёт 401 и закольцуется. И формулировка другая:
 *  человек не выходил сам, его выкинуло.
 */
function sessionExpired() {
  if (!getToken()) return;
  clearToken();
  initTheme();
  disconnectLive();
  applyAdminVisibility(false);
  document.getElementById('appWrapper').classList.remove('active');
  document.getElementById('loginScreen').classList.remove('hidden');
  document.querySelectorAll('.dropdown').forEach((d) => d.classList.remove('show'));
  // Тот же сигнал, что и при выходе по кнопке: разделы глушат свои поллеры.
  // Без него после 401 фоновые опросы продолжали бы молотить по закрытой
  // сессии. Личные ключи localStorage при этом НЕ чистим: истечение сессии —
  // не смена пользователя, фильтры пригодятся после повторного входа.
  window.dispatchEvent(new CustomEvent('talaria:logout'));
  toast('Сессия истекла — войдите заново', 'warn');
}

// Регистрируем сразу на уровне модуля: первые запросы (tryAutoLogin) уходят
// до любой инициализации, и обработчик должен уже стоять.
setUnauthorizedHandler(sessionExpired);

function applyUser(user) {
  if (!user) return;
  // Сохраняем текущего пользователя: его показывает карточка Telegram-аккаунта
  // в настройках. Выпадающего меню профиля больше нет, поэтому ни #ddProfile,
  // ни .avatar-btn здесь уже не трогаем.
  window.__me = user;
  const name = String(user.name || user.username || 'Пользователь').trim();
  const username = user.username
    ? `@${String(user.username).replace(/^@/, '')}`
    : 'Telegram подключён';
  const initial = (name[0] || 'O').toUpperCase();
  const sessionName = document.getElementById('sessionUserName');
  const sessionUsername = document.getElementById('sessionUserUsername');
  const sessionAvatar = document.getElementById('sessionUserAvatar');
  if (sessionName) sessionName.textContent = name;
  if (sessionUsername) sessionUsername.textContent = username;
  if (sessionAvatar) {
    sessionAvatar.textContent = initial;
    sessionAvatar.style.backgroundImage = '';
    if (/^https:\/\//i.test(user.photo_url || '')) {
      sessionAvatar.style.backgroundImage = `url("${String(user.photo_url).replace(/["\\]/g, '')}")`;
      sessionAvatar.textContent = '';
    }
  }
  const greeting = document.getElementById('dashGreeting');
  if (greeting) greeting.textContent = name ? `Добрый день, ${name}` : 'Главная';
  // Раздел «Админ» и навигация к нему показываются только админу.
  applyAdminVisibility(!!user.is_admin);
  applyViewOnly(!!user.view_only, name);
}

/**
 * Полоса «вы смотрите чужой кабинет».
 *
 * Без неё режим просмотра выглядит как сломавшийся сайт: кнопки нажимаются,
 * а ничего не сохраняется. Полоса висит сверху и не закрывается — забыть, что
 * находишься в чужом кабинете, не должно быть возможно.
 */
function applyViewOnly(active, name) {
  document.body.classList.toggle('view-only', active);
  let bar = document.getElementById('viewOnlyBar');
  if (!active) {
    bar?.remove();
    return;
  }
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'viewOnlyBar';
    bar.className = 'view-only-bar';
    document.body.prepend(bar);
  }
  // Собираем узлами, а не разметкой строкой: в auth.js попадают имена из
  // Telegram, и склейка HTML здесь — прямая дорога к подставленному скрипту.
  bar.replaceChildren();
  const text = document.createElement('span');
  text.append('Режим просмотра: кабинет ');
  const who = document.createElement('b');
  who.textContent = name || 'пользователя';
  text.append(who, '. Изменения недоступны.');
  const back = document.createElement('button');
  back.className = 'btn-mini';
  back.setAttribute('data-act', 'exitViewAs');
  back.textContent = 'Вернуться в свой аккаунт';
  bar.append(text, back);
}

/** Данные, которые нужны сразу после входа.
 *
 *  allSettled, а не all: упавший запрос (скажем, кампании) не должен оставить
 *  остальные страницы пустыми. Порядок неважен — все независимы.
 */
async function loadUserData() {
  await Promise.allSettled([
    loadDatabases(), // базы лидов
    loadOffers(), // офферы + селект офферов в рассылке
    loadPlaceholders(), // чипы подстановок
    loadAiOfferSettings(true), // скелет ИИ-оффера: его показывает и форма рассылки
    refreshAiAvailability(), // доступен ли ИИ-оффер на тарифе
    loadCampaignBases(), // селект баз получателей
    fillTestSendOffers(), // селект офферов в тестовой отправке
    loadCampaigns(),
    loadSettings(), // лимит (только чтение) + статус ИИ
    loadKeys(), // ключи YouTube + квота сайдбара
    loadWarmup(), // Telegram-аккаунты и прогрев
    loadSubscription(), // подписка: тариф, лимиты, «мой план»
    loadNotifications(), // лента уведомлений
    window.__isAdmin ? loadAdmin() : Promise.resolve(), // админ-сводка
  ]);
}

// Признак «в этой вкладке кабинет уже открывали». Живёт в sessionStorage: при
// F5 остаётся, при закрытии вкладки исчезает. Благодаря этому обновление
// страницы возвращает в тот же раздел (просьба владельца), а первый заход на
// сайт по-прежнему показывает лендинг — автовход остаётся запрещённым.
const IN_APP_KEY = 'talaria_in_app';

function markInApp(on) {
  try {
    if (on) sessionStorage.setItem(IN_APP_KEY, '1');
    else sessionStorage.removeItem(IN_APP_KEY);
  } catch (_) {
    /* приватный режим: обновление вернёт на лендинг, это допустимо */
  }
}

function wasInApp() {
  try {
    return sessionStorage.getItem(IN_APP_KEY) === '1';
  } catch (_) {
    return false;
  }
}

export function enterApp(user, options = {}) {
  resetUnauthorized(); // новая сессия — снова разрешаем предупреждение о протухании
  markInApp(true);
  initTheme(); // применить сохранённый шрифт и акцент рабочего пространства
  document.getElementById('loginScreen').classList.add('hidden');
  document.getElementById('appWrapper').classList.add('active');
  closeTelegramLoginDialog();
  applyUser(user);
  restoreLastSection();
  const silent = !!options.silent;
  setTimeout(() => {
    animateCounters(); // тянет /api/stats, сам строит график и грузит ленту
    loadUserData(); // раньше грузились только офферы и кампании
    connectLive(); // живая лента событий по WebSocket
    // При обновлении страницы (silent) здороваться заново не нужно: человек
    // никуда не входил, он просто нажал F5.
    if (!silent) toast(user && user.name ? `Привет, ${user.name}!` : 'Успешный вход', 'success');
    maybeShowOnboarding(); // покажется только при первом входе
  }, 400);
}

// Персональные следы в localStorage. Ключи объявлены строками, а не импортами:
// их владельцы (search.js, chat.js, …) сами не знают про logout, а имена
// менять нельзя — по ним читается сохранённое состояние (см. AI_HANDOFF.md).
const PERSONAL_STORAGE_KEYS = [
  'talaria.search-filters.v1', // фильтры поиска — коммерческая информация о нишах
  'talaria.chat-filters.v1',
  'talaria_notif_seen',
  'mf_onboarded',
  'mf_quickstart_hidden',
];

function clearPersonalStorage() {
  // Выход — это в том числе «передаю машину другому человеку»: фильтры ниш,
  // прочитанность уведомлений и флаги онбординга не должны доставаться
  // следующему вошедшему. Тема и ширина сайдбара — настройки устройства,
  // их не трогаем.
  PERSONAL_STORAGE_KEYS.forEach((key) => {
    try {
      localStorage.removeItem(key);
    } catch (_) {
      /* приватный режим — там и так ничего не сохранялось */
    }
  });
}

export function logout() {
  // Отозвать токен на сервере (jti уходит в чёрный список). Ошибки не
  // блокируют выход: даже если бэкенд недоступен, локально выходим.
  if (getToken()) {
    api('POST', '/api/auth/logout').catch(() => {});
  }
  disconnectLive(); // закрыть WebSocket и перестать переподключаться
  markInApp(false);
  clearToken();
  clearPersonalStorage();
  // Разослать модулям с фоновыми опросами (чат, кампании, поиск, поддержка)
  // команду остановиться: иначе их таймеры продолжали бы стучать в API с
  // мёртвым токеном и получать тихие 401 до перезагрузки страницы.
  window.dispatchEvent(new CustomEvent('talaria:logout'));
  initTheme(); // гостевой лендинг: Talaria pink + Unbounded/JetBrains Mono
  applyAdminVisibility(false);
  document.getElementById('appWrapper').classList.remove('active');
  document.getElementById('loginScreen').classList.remove('hidden');
  document.querySelectorAll('.dropdown').forEach((d) => d.classList.remove('show'));
  window.__me = null;
  toast('Вы вышли из системы', 'success', 'Сессия закрыта. Данные кампаний сохранены.');
}

// Живая сессия больше НЕ втаскивает человека в кабинет сама.
//
// Раньше открытие адреса сайта у залогиненного человека сразу подменяло лендинг
// кабинетом: посмотреть тариф или показать кому-то главную было невозможно без
// выхода из системы. Теперь при живом токене мы остаёмся на лендинге и лишь
// запоминаем, что вход возможен без Telegram — кнопка «Войти» тогда открывает
// кабинет мгновенно, одним кликом, ничего не переспрашивая.
let warmSession = null;

export function hasWarmSession() {
  return warmSession !== null;
}

export function enterWithWarmSession() {
  if (!warmSession) return false;
  const me = warmSession;
  warmSession = null;
  delete document.documentElement.dataset.session;
  enterApp(me);
  return true;
}

/** Кнопка «Войти» превращается в аватар и имя, если сессия ещё жива.
 *
 *  Человек, который уже входил, видит себя, а не приглашение войти заново.
 *  Клик по-прежнему ведёт в кабинет — вход мгновенный, без Telegram.
 */
function showWarmSessionChip(me) {
  const cta = document.getElementById('navLoginCta');
  const ava = document.getElementById('navLoginAva');
  const label = document.getElementById('navLoginLabel');
  if (!cta || !ava || !label) return;
  const name = String(me?.name || me?.username || 'Войти').trim();
  label.textContent = name.length > 18 ? `${name.slice(0, 17)}…` : name;
  ava.textContent = (name[0] || 'T').toUpperCase();
  // Только https: chrome-extension:// и data: в фоне картинки — чужой контент
  // на нашей странице.
  if (/^https:\/\//i.test(me?.photo_url || '')) {
    ava.style.backgroundImage = `url("${String(me.photo_url).replace(/["\\]/g, '')}")`;
    ava.textContent = '';
  }
  cta.classList.add('has-session');
  cta.setAttribute('title', 'Открыть рабочее пространство');
}

async function tryAutoLogin() {
  if (!getToken()) return false;
  try {
    const me = await api('GET', '/api/me');
    resetUnauthorized();
    warmSession = me;
    // Признак в разметке: сессия жива, вход будет мгновенным. По нему
    // ориентируются тесты, и по нему же при желании можно поменять надпись на
    // кнопке — «Войти» вместо «Войти через Telegram».
    document.documentElement.dataset.session = 'warm';
    showWarmSessionChip(me);
    if (wasInApp()) {
      // Это обновление страницы внутри кабинета, а не заход на сайт: возвращаем
      // человека туда же, где он был, без приветствия и без экрана входа.
      warmSession = null;
      delete document.documentElement.dataset.session;
      enterApp(me, { silent: true });
      return true;
    }
    return false; // токен жив, но вход — только по клику
  } catch (err) {
    // Токен истёк или подписан другим секретом (401/403). Здесь это не
    // «выкинуло», а обычный вход с нуля — тост про истёкшую сессию был бы
    // шумом на экране входа, поэтому снимаем токен тихо и гасим флаг.
    if (err && (err.status === 401 || err.status === 403)) {
      clearToken();
      resetUnauthorized();
      return false;
    }
    // Сеть или 5xx: бэкенд прилёг, но токен может быть жив. Разлогинивать
    // всех при каждом рестарте сервера нельзя — оставляем токен на месте,
    // вход просто останется «по клику».
    resetUnauthorized();
    return false;
  }
}

export async function initAuth() {
  // Тему инициализирует main.js (boot) — повторной инициализации здесь нет.
  bindTelegramLoginTriggers();
  // Публичные тарифы не должны ждать проверки старого токена или Telegram
  // redirect: начинаем загрузку сразу, параллельно всей авторизации.
  loadPublicPlans();
  const redirected = await consumeTelegramRedirect();
  if (redirected.authenticated) return;
  if (await tryAutoLogin()) return;
  try {
    const cfg = await api('GET', '/api/config');
    if (cfg.demo_mode || !cfg.bot_username) showUnavailableLogin();
    else {
      bindDirectTelegramLogin(cfg.bot_id);
      mountTelegramWidget(cfg.bot_username);
      if (redirected.found) {
        setHint(
          redirected.error
            ? `Вход не выполнен: ${redirected.error}`
            : 'Вход не выполнен. Повторите попытку.',
          true,
        );
      }
    }
  } catch (e) {
    setHint(`Бэкенд недоступен: ${e.message}`, true);
  }
}
