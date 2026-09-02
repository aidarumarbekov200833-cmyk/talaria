/* Лендинг v53 — поведение. Классический скрипт без модулей: работает до
   загрузки main.js и не зависит от бэкенда. Живые цифры и reveal-класс
   is-inview ставит js/landing.js (через polish.js), здесь остальное:
   тема, мобильное меню, активный пункт шапки, переписка в телефоне, липкие
   шаги, печать письма, FAQ-переписка. Всё гасится, если #loginScreen скрыт. */
(function () {
  'use strict';
  var screen = document.getElementById('loginScreen');
  if (!screen) return;
  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)');
  function noMotion() {
    return Boolean(reduced && reduced.matches);
  }
  function hidden() {
    return getComputedStyle(screen).display === 'none';
  }

  /* --- тема: общая с кабинетом (mf_theme), дефолт тёмная --- */
  (function theme() {
    var root = document.documentElement;
    var sw = document.getElementById('tsw');
    var saved = null;
    try {
      saved = localStorage.getItem('mf_theme');
    } catch (e) {}
    if (!saved) {
      saved = 'dark';
      try {
        localStorage.setItem('mf_theme', saved);
      } catch (e) {}
    }
    root.dataset.theme = saved;
    function sync() {
      if (sw) sw.setAttribute('aria-pressed', String(root.dataset.theme === 'light'));
    }
    sync();
    if (sw)
      sw.addEventListener('click', function () {
        root.dataset.theme = root.dataset.theme === 'light' ? 'dark' : 'light';
        try {
          localStorage.setItem('mf_theme', root.dataset.theme);
        } catch (e) {}
        sync();
      });
  })();

  /* --- мобильное меню --- */
  (function burger() {
    var btn = document.getElementById('tlBurger');
    var links = document.getElementById('tlLinks');
    if (!btn || !links) return;
    function set(open) {
      links.classList.toggle('is-open', open);
      btn.setAttribute('aria-expanded', String(open));
    }
    btn.addEventListener('click', function () {
      set(!links.classList.contains('is-open'));
    });
    links.addEventListener('click', function (e) {
      if (e.target.tagName === 'A') set(false);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') set(false);
    });
  })();

  /* --- активный пункт шапки по видимой секции --- */
  (function activeLink() {
    var links = Array.prototype.slice.call(screen.querySelectorAll('.tl-links a[href^="#"]'));
    if (!links.length || !('IntersectionObserver' in window)) return;
    var map = {};
    links.forEach(function (a) {
      var id = a.getAttribute('href').slice(1);
      var sec = document.getElementById(id);
      if (sec) map[id] = a;
    });
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          links.forEach(function (a) {
            a.classList.remove('is-on');
          });
          var a = map[en.target.id];
          if (a) a.classList.add('is-on');
        });
      },
      { rootMargin: '-45% 0px -45% 0px' },
    );
    Object.keys(map).forEach(function (id) {
      io.observe(document.getElementById(id));
    });
  })();

  /* --- телефон: одно письмо с превью, один ответ, пуш от бота, цикл --- */
  (function phone() {
    var chat = document.getElementById('chat');
    var sub = document.getElementById('tgSub');
    var notice = document.getElementById('phNotice');
    if (!chat) return;
    function tm(t, checks) {
      return '<span class="tm">' + t + (checks ? ' ✓✓' : '') + '</span>';
    }
    /* Превью оффера — рисованная карточка (inline svg), в цвет темы. */
    var PHOTO =
      '<span class="tg-photo"><svg viewBox="0 0 320 170" role="img" aria-label="Превью оффера">' +
      '<rect width="320" height="170" fill="currentColor" opacity=".08"/>' +
      '<path d="M22 126 70 98l34 16 40-38 36 20 42-52 40 22" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" opacity=".9"/>' +
      '<circle cx="144" cy="76" r="4.4" fill="currentColor"/><circle cx="222" cy="44" r="4.4" fill="currentColor"/>' +
      '<text x="22" y="40" fill="currentColor" font-family="Inter, sans-serif" font-size="15" font-weight="600">Аналитика портфеля</text>' +
      '<text x="22" y="60" fill="currentColor" opacity=".6" font-family="Inter, sans-serif" font-size="11">интеграция в ролик · медиакит</text>' +
      '<rect x="22" y="138" width="88" height="20" rx="10" fill="currentColor"/>' +
      '<text x="36" y="152" fill="currentColor" opacity=".08" font-family="Inter, sans-serif" font-size="10" font-weight="700">МЕДИАКИТ</text>' +
      '</svg></span>';
    var SEQ = [
      { c: 'tg-date', d: 400, html: 'Сегодня' },
      {
        c: 'tg-out',
        d: 1300,
        html:
          PHOTO +
          '<span class="tg-txt">Привет! Смотрел ваш разбор портфеля на 2026, зашла часть про облигации. Собрали медиакит под ваш канал: интеграция, бюджет обсуждаем. Интересно?</span>' +
          tm('14:02', true),
      },
      {
        c: 'tg-in',
        d: 1700,
        typing: true,
        html:
          '<span class="tg-txt">Привет! Да, звучит интересно. Скиньте детали, посмотрю после съёмок 👍</span>' +
          tm('14:09'),
      },
      { notice: true, d: 1100 },
    ];
    var timer = null;
    var running = false;
    function add(m) {
      var el = document.createElement('div');
      el.className = m.c === 'tg-date' ? m.c : 'tg-msg ' + m.c;
      el.innerHTML = m.html;
      chat.appendChild(el);
      if (el.classList.contains('tg-msg'))
        requestAnimationFrame(function () {
          requestAnimationFrame(function () {
            el.classList.add('show');
          });
        });
      while (chat.children.length > 6) chat.removeChild(chat.firstChild);
    }
    function setNotice(on) {
      if (!notice) return;
      notice.classList.toggle('on', on);
      notice.setAttribute('aria-hidden', String(!on));
    }
    function play(i) {
      if (!running) return;
      if (i >= SEQ.length) {
        timer = setTimeout(function () {
          chat.innerHTML = '';
          setNotice(false);
          play(0);
        }, 5600);
        return;
      }
      var m = SEQ[i];
      timer = setTimeout(function () {
        if (m.notice) {
          setNotice(true);
          play(i + 1);
        } else if (m.typing) {
          if (sub) {
            sub.textContent = 'печатает…';
            sub.classList.add('typ');
          }
          var tp = document.createElement('div');
          tp.className = 'tg-msg tg-in show';
          tp.innerHTML =
            '<span class="tg-typing"><i style="--td:0s"></i><i style="--td:.15s"></i><i style="--td:.3s"></i></span>';
          chat.appendChild(tp);
          timer = setTimeout(
            function () {
              tp.remove();
              if (sub) {
                sub.textContent = 'в сети';
                sub.classList.remove('typ');
              }
              add(m);
              play(i + 1);
            },
            noMotion() ? 200 : 1600,
          );
        } else {
          add(m);
          play(i + 1);
        }
      }, m.d);
    }
    function start() {
      if (running || hidden()) return;
      running = true;
      play(0);
    }
    function stop() {
      running = false;
      clearTimeout(timer);
    }
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(
        function (entries) {
          entries.forEach(function (en) {
            if (en.isIntersecting) start();
            else stop();
          });
        },
        { threshold: 0.2 },
      ).observe(chat);
    } else start();
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) stop();
      else start();
    });
  })();

  /* --- reveal: дублирует js/landing.js нарочно — если у посетителя закеширован
     старый landing.js со старыми селекторами, панели всё равно проявятся. --- */
  (function reveal() {
    var nodes = Array.prototype.slice.call(screen.querySelectorAll('.rv'));
    if (!nodes.length) return;
    if (noMotion() || !('IntersectionObserver' in window)) {
      nodes.forEach(function (n) {
        n.classList.add('is-inview');
      });
      return;
    }
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) {
            en.target.classList.add('is-inview');
            io.unobserve(en.target);
          }
        });
      },
      { threshold: 0.18 },
    );
    nodes.forEach(function (n) {
      io.observe(n);
    });
  })();

  /* --- шаги: подсветка в липкой колонке по самому видимому экрану --- */
  (function steps() {
    var items = Array.prototype.slice.call(screen.querySelectorAll('#tlSteps li'));
    var panels = Array.prototype.slice.call(screen.querySelectorAll('.tl-panel[data-step]'));
    if (!items.length || !panels.length || !('IntersectionObserver' in window)) return;
    var ratios = {};
    function pick() {
      var best = null;
      panels.forEach(function (p) {
        var r = ratios[p.dataset.step] || 0;
        if (!best || r > (ratios[best] || 0)) best = p.dataset.step;
      });
      items.forEach(function (li) {
        li.classList.toggle('is-on', li.dataset.step === best);
      });
      // Линия прогресса слева от списка: заполняется до активного шага.
      var idx = Math.max(0, Number(best) - 1);
      var list = items[0].parentNode;
      list.style.setProperty(
        '--tl-step-progress',
        String(Math.round((idx / Math.max(1, items.length - 1)) * 100)) + '%',
      );
    }
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (en) {
          ratios[en.target.dataset.step] = en.isIntersecting ? en.intersectionRatio : 0;
        });
        pick();
      },
      { threshold: [0, 0.25, 0.5, 0.75, 1] },
    );
    panels.forEach(function (p) {
      io.observe(p);
    });
  })();

  /* --- письмо печатается, когда экран №3 виден; повтор циклом --- */
  (function compose() {
    var box = document.getElementById('tlCompose');
    if (!box) return;
    var TEXT =
      'Привет! Смотрел ваш разбор портфеля на 2026, зашла часть про облигации. Собрали медиакит под ваш канал: интеграция, бюджет обсуждаем. Интересно?';
    var timer = null;
    var running = false;
    function type(i) {
      if (!running) return;
      if (i > TEXT.length) {
        box.classList.add('done');
        timer = setTimeout(function () {
          box.classList.remove('done');
          type(0);
        }, 5200);
        return;
      }
      box.textContent = TEXT.slice(0, i);
      var ch = TEXT.charAt(i - 1);
      timer = setTimeout(type, /[,.!?]/.test(ch) ? 220 : 26, i + 1);
    }
    function start() {
      if (running) return;
      running = true;
      if (noMotion()) {
        box.textContent = TEXT;
        box.classList.add('done');
        return;
      }
      type(0);
    }
    function stop() {
      running = false;
      clearTimeout(timer);
    }
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(
        function (entries) {
          entries.forEach(function (en) {
            if (en.isIntersecting) start();
            else stop();
          });
        },
        { threshold: 0.3 },
      ).observe(box);
    } else start();
  })();

  /* --- цены тарифа: один запрос на страницу, делят FAQ и блок тарифа --- */
  var plansPromise = null;
  function loadPlans() {
    if (plansPromise) return plansPromise;
    plansPromise = fetch('/api/plans', { headers: { Accept: 'application/json' } })
      .then(function (r) {
        return r.ok ? r.json() : null;
      })
      .then(function (data) {
        return (data && data.plans && data.plans[0]) || null;
      })
      .catch(function () {
        return null;
      });
    return plansPromise;
  }
  function money(v) {
    return Number(v || 0).toLocaleString('ru-RU');
  }

  /* --- v55: цена как заголовок — зеркало карточки tariffs.js. Ждём, пока карточка
     отрисуется, копируем [data-plan-price]/[data-plan-caption], клики по большим
     переключателям проксируем в её кнопки, чтобы расчёт был один (серверный). --- */
  (function priceBig() {
    var big = document.getElementById('tlPriceBig');
    var grid = document.getElementById('landingTariffGrid');
    var num = document.getElementById('tlBigPrice');
    var cap = document.getElementById('tlBigCap');
    var section = document.getElementById('pricing');
    if (!big || !grid || !num || !cap || !section) return;
    var card = null;
    function sync() {
      var p = card.querySelector('[data-plan-price]');
      var c = card.querySelector('[data-plan-caption]');
      if (p) num.textContent = p.textContent;
      if (c) cap.textContent = c.textContent;
      big.classList.toggle('is-swapping', card.classList.contains('is-swapping'));
      Array.prototype.forEach.call(big.querySelectorAll('[data-big-period]'), function (b) {
        var src = card.querySelector('[data-plan-period="' + b.dataset.bigPeriod + '"]');
        var on = Boolean(src && src.classList.contains('active'));
        b.classList.toggle('is-on', on);
        b.setAttribute('aria-pressed', String(on));
      });
      Array.prototype.forEach.call(big.querySelectorAll('[data-big-currency]'), function (b) {
        var src = card.querySelector('[data-plan-currency="' + b.dataset.bigCurrency + '"]');
        var on = Boolean(src && src.classList.contains('active'));
        b.classList.toggle('is-on', on);
        b.setAttribute('aria-pressed', String(on));
      });
    }
    function attach(found) {
      card = found;
      big.hidden = false;
      section.classList.add('has-big');
      sync();
      new MutationObserver(sync).observe(card, {
        subtree: true,
        childList: true,
        characterData: true,
        attributes: true,
        attributeFilter: ['class'],
      });
    }
    big.addEventListener('click', function (e) {
      var b = e.target.closest('[data-big-period],[data-big-currency]');
      if (!b || !card) return;
      var sel = b.dataset.bigPeriod
        ? '[data-plan-period="' + b.dataset.bigPeriod + '"]'
        : '[data-plan-currency="' + b.dataset.bigCurrency + '"]';
      var src = card.querySelector(sel);
      if (src && !src.classList.contains('active')) src.click();
    });
    var existing = grid.querySelector('[data-plan-card]');
    if (existing) {
      attach(existing);
      return;
    }
    var mo = new MutationObserver(function () {
      var found = grid.querySelector('[data-plan-card]');
      if (!found) return;
      mo.disconnect();
      attach(found);
    });
    mo.observe(grid, { childList: true });
  })();

  /* --- v55: FAQ как чат. Список dl — источник; с JS он скрыт, вопросы становятся кнопками,
     ответ приходит в окно: пузырь вопроса → «печатает…» → посимвольная печать ответа. --- */
  (function faqChat() {
    var src = document.getElementById('tlFaq');
    var qs = document.getElementById('tlFaqQs');
    var chat = document.getElementById('tlChat');
    var log = document.getElementById('tlChatLog');
    var status = document.getElementById('tlChatStatus');
    var count = document.getElementById('tlChatCount');
    var foot = document.getElementById('tlChatFoot');
    if (!src || !qs || !chat || !log) return;
    var items = [];
    var dts = Array.prototype.slice.call(src.querySelectorAll('dt'));
    dts.forEach(function (dt) {
      var dd = dt.nextElementSibling;
      if (!dd || dd.tagName !== 'DD') return;
      items.push({
        q: dt.textContent.trim(),
        a: dd.textContent.replace(/\s+/g, ' ').trim(),
        price: dd.hasAttribute('data-price'),
      });
    });
    if (!items.length) return;
    src.hidden = true;
    chat.hidden = false;
    var asked = 0;
    var busy = false;
    var timers = [];
    function later(fn, ms) {
      timers.push(setTimeout(fn, ms));
    }
    function clock() {
      var d = new Date();
      return (
        (d.getHours() < 10 ? '0' : '') +
        d.getHours() +
        ':' +
        (d.getMinutes() < 10 ? '0' : '') +
        d.getMinutes()
      );
    }
    function scrollDown() {
      log.scrollTop = log.scrollHeight;
    }
    function bubble(cls, text) {
      var el = document.createElement('div');
      el.className = 'tl-cm ' + cls;
      var t = document.createElement('span');
      t.className = 'tl-cm-txt';
      t.textContent = text;
      var m = document.createElement('span');
      m.className = 'tl-cm-time';
      m.textContent = clock() + (cls === 'is-out' ? ' \u2713\u2713' : '');
      el.appendChild(t);
      el.appendChild(m);
      log.appendChild(el);
      requestAnimationFrame(function () {
        el.classList.add('show');
        scrollDown();
      });
      return { el: el, txt: t };
    }
    function setCount() {
      if (count) count.textContent = asked + ' из ' + items.length;
    }
    function priceText(item, plan) {
      if (!item.price) return item.a;
      if (!plan)
        return 'Один тариф за всё. Цена в рублях и тенге показана в блоке «Тариф» выше, за три месяца сразу — минус 15%.';
      return item.a
        .replace('{rub}', money(plan.price_rub) + ' \u20bd')
        .replace('{kzt}', money(plan.price_kzt) + ' \u20b8');
    }
    function ask(item, btn) {
      if (busy || btn.disabled) return;
      busy = true;
      btn.disabled = true;
      btn.classList.add('is-asked');
      asked += 1;
      setCount();
      if (foot)
        foot.textContent = asked >= items.length ? 'Остальное — @Cxentrall' : 'Следующий вопрос?';
      var r = chat.getBoundingClientRect();
      if (r.top < 0 || r.bottom > window.innerHeight) {
        chat.scrollIntoView({ block: 'start', behavior: noMotion() ? 'auto' : 'smooth' });
      }
      bubble('is-out', item.q);
      var instant = noMotion();
      var typing = document.createElement('div');
      typing.className = 'tl-cm is-in tl-cm-typing';
      typing.setAttribute('aria-hidden', 'true');
      typing.innerHTML = '<i style="--td:0s"></i><i style="--td:.15s"></i><i style="--td:.3s"></i>';
      var answer = function (text) {
        typing.remove();
        if (status) status.textContent = 'отвечает быстро';
        var b = bubble('is-in', instant ? text : '');
        if (instant) {
          busy = false;
          return;
        }
        var i = 0;
        var step = function () {
          i += text.length > 160 ? 3 : 2;
          b.txt.textContent = text.slice(0, i);
          scrollDown();
          if (i < text.length) later(step, 14);
          else busy = false;
        };
        step();
      };
      var plan = loadPlans();
      later(
        function () {
          if (status) status.textContent = 'печатает\u2026';
          log.appendChild(typing);
          requestAnimationFrame(function () {
            typing.classList.add('show');
            scrollDown();
          });
          later(
            function () {
              plan.then(function (p) {
                answer(priceText(item, p));
              });
            },
            instant ? 0 : 700,
          );
        },
        instant ? 0 : 420,
      );
    }
    items.forEach(function (item, i) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'tl-fq';
      btn.textContent = item.q;
      btn.style.setProperty('--i', String(i));
      btn.addEventListener('click', function () {
        ask(item, btn);
      });
      qs.appendChild(btn);
    });
    setCount();
    /* первый вопрос задаётся сам, когда окно попадает в кадр */
    var auto = function () {
      var first = qs.querySelector('.tl-fq:not(.is-asked)');
      if (first) ask(items[0], first);
    };
    if (!('IntersectionObserver' in window)) {
      auto();
      return;
    }
    var seen = false;
    new IntersectionObserver(
      function (entries, obs) {
        entries.forEach(function (e) {
          if (!e.isIntersecting || seen) return;
          seen = true;
          obs.disconnect();
          later(auto, noMotion() ? 0 : 500);
        });
      },
      { threshold: 0.35 },
    ).observe(chat);
  })();

  /* --- v54: письмо по нишам — вкладки, печать, автосмена до первого клика --- */
  (function letters() {
    var root = document.getElementById('tlLetter');
    if (!root) return;
    var tabs = Array.prototype.slice.call(root.querySelectorAll('[data-niche]'));
    var ava = document.getElementById('tlLetterAva');
    var name = document.getElementById('tlLetterName');
    var meta = document.getElementById('tlLetterMeta');
    var chips = document.getElementById('tlLetterChips');
    var text = document.getElementById('tlLetterText');
    var count = document.getElementById('tlLetterCount');
    if (!tabs.length || !text) return;
    var DATA = [
      {
        ava: 'И',
        name: 'Инвестируй Просто',
        meta: '142 тыс. · RU · @invest_prosto',
        chips: ['ролик: «Разбор портфеля на 2026»', 'тема: облигации'],
        text: 'Привет! Смотрел ваш разбор портфеля на 2026, зашла часть про облигации. Собрали медиакит под ваш канал: интеграция в ролик, бюджет обсуждаем. Интересно?',
      },
      {
        ava: 'М',
        name: 'Тренер Марина',
        meta: '61 тыс. · RU · @marina_fit',
        chips: ['ролик: «Тренировка без инвентаря»', 'тема: домашний фитнес'],
        text: 'Марина, привет! Посмотрел тренировку без инвентаря — разминка в начале сделана честно, без «пропустите её». У нас приложение с планами питания под домашние тренировки, ищем тренеров на интеграцию. Обсудим формат?',
      },
      {
        ava: 'К',
        name: 'Код без боли',
        meta: '89 тыс. · RU · @painless_code',
        chips: ['ролик: «Разбор собеседования в бэкенд»', 'тема: Python, системный дизайн'],
        text: 'Здравствуйте! Смотрел разбор собеседования — момент про идемпотентность объяснён лучше, чем в половине курсов. Мы делаем платформу мок-интервью с живыми инженерами и хотим предложить интеграцию в следующий разбор. Интересно?',
      },
      {
        ava: 'Д',
        name: 'Готовим с Дашей',
        meta: '204 тыс. · RU · @dasha_cooks',
        chips: ['ролик: «Ужин за 20 минут»', 'тема: быстрые рецепты'],
        text: 'Даша, привет! Ужин за 20 минут пересмотрел дважды, соус из второго рецепта уже в холодильнике. Мы делаем наборы продуктов с рецептами на неделю — хотим предложить интеграцию с промокодом для ваших зрителей. Обсудим?',
      },
      {
        ava: 'С',
        name: 'Спокойно',
        meta: '47 тыс. · RU · @spokoyno_tg',
        chips: ['ролик: «Как перестать откладывать»', 'тема: прокрастинация, привычки'],
        text: 'Здравствуйте! Ролик про откладывание — редкий случай, когда советы не сводятся к «просто начни». У нас сервис онлайн-психологов, ищем авторов с таким же тоном для честной интеграции. Расскажу условия?',
      },
    ];
    var current = 0;
    var timer = null;
    var rotate = null;
    var userTouched = false;
    var visible = false;

    function stop() {
      if (timer) clearTimeout(timer);
      timer = null;
    }
    function typeInto(str, i) {
      if (i > str.length) {
        root.classList.add('is-done');
        if (!userTouched && visible) {
          rotate = setTimeout(function () {
            show((current + 1) % DATA.length);
          }, 5200);
        }
        return;
      }
      text.textContent = str.slice(0, i);
      if (count) count.textContent = i + ' ' + plural(i, ['знак', 'знака', 'знаков']);
      var ch = str.charAt(i - 1);
      var delay =
        ch === '.' || ch === '?' || ch === '!' ? 260 : ch === ',' ? 120 : 16 + Math.random() * 26;
      timer = setTimeout(function () {
        typeInto(str, i + 1);
      }, delay);
    }
    function plural(n, forms) {
      var m10 = n % 10;
      var m100 = n % 100;
      if (m10 === 1 && m100 !== 11) return forms[0];
      if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return forms[1];
      return forms[2];
    }
    function show(i) {
      stop();
      if (rotate) clearTimeout(rotate);
      rotate = null;
      current = i;
      var d = DATA[i];
      tabs.forEach(function (t, k) {
        t.setAttribute('aria-selected', String(k === i));
      });
      if (ava) ava.textContent = d.ava;
      if (name) name.textContent = d.name;
      if (meta) meta.textContent = d.meta;
      if (chips) {
        chips.innerHTML = '';
        d.chips.concat(['Telegram найден']).forEach(function (c, k, arr) {
          var sp = document.createElement('span');
          sp.textContent = c;
          if (k === arr.length - 1) sp.className = 'is-on';
          chips.appendChild(sp);
        });
      }
      root.classList.remove('is-done');
      if (noMotion()) {
        text.textContent = d.text;
        if (count)
          count.textContent =
            d.text.length + ' ' + plural(d.text.length, ['знак', 'знака', 'знаков']);
        root.classList.add('is-done');
        return;
      }
      text.textContent = '';
      typeInto(d.text, 1);
    }
    tabs.forEach(function (t, k) {
      t.addEventListener('click', function () {
        userTouched = true;
        show(k);
      });
    });
    // Печатать только когда блок на экране: печать вне экрана — пустая работа.
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(
        function (entries) {
          entries.forEach(function (en) {
            visible = en.isIntersecting;
            if (visible && !timer && !root.classList.contains('is-done')) show(current);
            if (visible && root.classList.contains('is-done') && !userTouched && !rotate)
              show((current + 1) % DATA.length);
            if (!visible) {
              stop();
              if (rotate) clearTimeout(rotate);
              rotate = null;
            }
          });
        },
        { threshold: 0.35 },
      ).observe(root);
    } else {
      show(0);
    }
  })();

  /* --- v54: телефон в герое чуть следует за курсором (только мышь, без reduced-motion) --- */
  (function tilt() {
    var phone = screen.querySelector('.tl-phone');
    var hero = screen.querySelector('.tl-hero');
    if (!phone || !hero) return;
    if (noMotion()) return;
    if (!window.matchMedia || !window.matchMedia('(hover: hover) and (pointer: fine)').matches)
      return;
    var raf = null;
    var rx = 0;
    var ry = 0;
    function apply() {
      raf = null;
      phone.style.transform =
        'perspective(1200px) rotateX(' + rx.toFixed(2) + 'deg) rotateY(' + ry.toFixed(2) + 'deg)';
    }
    hero.addEventListener('mousemove', function (e) {
      var r = phone.getBoundingClientRect();
      var cx = r.left + r.width / 2;
      var cy = r.top + r.height / 2;
      ry = ((e.clientX - cx) / window.innerWidth) * 10;
      rx = -((e.clientY - cy) / window.innerHeight) * 10;
      if (!raf) raf = requestAnimationFrame(apply);
    });
    hero.addEventListener('mouseleave', function () {
      rx = 0;
      ry = 0;
      if (!raf) raf = requestAnimationFrame(apply);
    });
  })();
})();
