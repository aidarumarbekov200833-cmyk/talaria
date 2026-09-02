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

  /* --- FAQ как переписка: клик по вопросу → «печатает…» → ответ --- */
  (function faq() {
    var list = document.getElementById('tlFaq');
    if (!list) return;
    var items = Array.prototype.slice.call(list.querySelectorAll('.tl-q'));
    items.forEach(function (d) {
      var summary = d.querySelector('summary');
      var answer = d.querySelector('.tl-a');
      if (!summary || !answer) return;
      var typing = null;
      var timer = null;
      summary.addEventListener('click', function (e) {
        e.preventDefault();
        if (d.open) {
          clearTimeout(timer);
          if (typing) {
            typing.remove();
            typing = null;
          }
          d.open = false;
          return;
        }
        items.forEach(function (o) {
          if (o !== d && o.open) o.open = false;
        });
        answer.hidden = true;
        d.open = true;
        if (noMotion()) {
          answer.hidden = false;
          return;
        }
        typing = document.createElement('div');
        typing.className = 'tl-typing';
        typing.setAttribute('aria-hidden', 'true');
        typing.innerHTML =
          '<i style="--td:0s"></i><i style="--td:.15s"></i><i style="--td:.3s"></i>';
        d.appendChild(typing);
        timer = setTimeout(function () {
          if (typing) {
            typing.remove();
            typing = null;
          }
          answer.hidden = false;
        }, 650);
      });
    });
  })();
})();
