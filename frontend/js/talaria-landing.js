/* Talaria V5 landing behaviour — exact port, runs only while #loginScreen is visible */
(function () {
  function __landingHidden() {
    var l = document.getElementById('loginScreen');
    return !l || getComputedStyle(l).display === 'none';
  }
  window.__talLandingHidden = __landingHidden;

  /* theme (bridged to app mf_theme; landing defaults dark like the design) */
  (function () {
    var sw = document.getElementById('tsw'),
      root = document.documentElement;
    try {
      var s = localStorage.getItem('mf_theme');
      if (!s) {
        s = 'dark';
        localStorage.setItem('mf_theme', 'dark');
      }
      root.dataset.theme = s;
    } catch (e) {
      root.dataset.theme = 'dark';
    }
    fix();
    sw &&
      sw.addEventListener('click', function () {
        root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
        try {
          localStorage.setItem('mf_theme', root.dataset.theme);
        } catch (e) {}
        fix();
      });
    function fix() {
      var i = sw && sw.querySelector('i');
      if (i) i.textContent = root.dataset.theme === 'dark' ? '\u263e' : '\u2600';
    }
  })();

  /* reveal */
  (function () {
    var io = new IntersectionObserver(
      function (es) {
        es.forEach(function (e) {
          if (e.isIntersecting) {
            e.target.classList.add('on');
            io.unobserve(e.target);
          }
        });
      },
      { threshold: 0.14 },
    );
    document.querySelectorAll('.rv:not(.on)').forEach(function (el) {
      io.observe(el);
    });
  })();

  /* hero starfield */
  (function () {
    var cv = document.getElementById('stars'),
      cx = cv.getContext('2d'),
      pts = [],
      W,
      H;
    function rs() {
      W = cv.width = cv.offsetWidth * devicePixelRatio;
      H = cv.height = cv.offsetHeight * devicePixelRatio;
    }
    rs();
    addEventListener('resize', rs);
    for (var i = 0; i < 70; i++)
      pts.push({
        x: Math.random(),
        y: Math.random(),
        vx: (Math.random() - 0.5) * 0.0006,
        vy: (Math.random() - 0.5) * 0.0006,
        r: Math.random() * 1.6 + 0.4,
      });
    (function fr() {
      cx.clearRect(0, 0, W, H);
      /* Токен --pink объявлен на #loginScreen, а не на <html>: с documentElement
         сюда приходила пустая строка, strokeStyle её игнорировал и оставался
         чёрным по умолчанию. На тёмной теме чёрное по чёрному не видно, а на
         светлой поверх героя шли чёрные линии и точки. Берём стиль с самого
         холста — он внутри #loginScreen и токен наследует. */
      var pink = getComputedStyle(cv).getPropertyValue('--pink').trim() || '#ff2fa4';
      pts.forEach(function (p) {
        p.x = (p.x + p.vx + 1) % 1;
        p.y = (p.y + p.vy + 1) % 1;
      });
      cx.strokeStyle = pink;
      cx.globalAlpha = 0.1;
      cx.lineWidth = 1;
      for (var i = 0; i < pts.length; i++)
        for (var j = i + 1; j < pts.length; j++) {
          var a = pts[i],
            b = pts[j],
            dx = (a.x - b.x) * W,
            dy = (a.y - b.y) * H,
            d = dx * dx + dy * dy,
            lim = 140 * devicePixelRatio;
          if (d < lim * lim) {
            cx.beginPath();
            cx.moveTo(a.x * W, a.y * H);
            cx.lineTo(b.x * W, b.y * H);
            cx.stroke();
          }
        }
      cx.globalAlpha = 0.55;
      cx.fillStyle = pink;
      pts.forEach(function (p) {
        cx.beginPath();
        cx.arc(p.x * W, p.y * H, p.r * devicePixelRatio, 0, 7);
        cx.fill();
      });
      requestAnimationFrame(fr);
    })();
  })();

  /* Воронка сервиса: настоящие числа из /api/public/stats.

     Раньше здесь крутилась выдуманная лента каналов («Инвестируй Просто» и
     компания). Её убрали: посетитель считывал её как реальную работу сервиса,
     а это была анимация. Теперь блок показывает фактическую воронку —
     найдено, отправлено, доставлено, ответы.

     Отдельная задача — не выглядеть мёртвым, пока цифры маленькие. Решаем
     вёрсткой, а не подкруткой чисел: у полосы есть минимальная видимая длина,
     пустая воронка получает «дышащее» состояние ожидания и честную подпись. */
  (function () {
    var list = document.getElementById('funList');
    if (!list) return;
    var note = document.getElementById('funnelNote');
    var rows = [].slice.call(list.querySelectorAll('.fun-row'));
    var MIN_BAR = 6; /* % — чтобы ненулевой шаг всегда был виден */

    function readStat(key) {
      var el = list.querySelector('[data-public-stat="' + key + '"]');
      if (!el) return 0;
      var n = parseInt(String(el.textContent).replace(/[^0-9]/g, ''), 10);
      return isFinite(n) ? n : 0;
    }

    function paint() {
      var values = rows.map(function (row) {
        return readStat(row.getAttribute('data-funnel'));
      });
      var top = Math.max.apply(null, values.concat([0]));
      var alive = top > 0;
      list.classList.toggle('idle', !alive);
      rows.forEach(function (row, i) {
        var bar = row.querySelector('.fun-bar i');
        if (!bar) return;
        var pct = alive && values[i] > 0 ? MIN_BAR + (values[i] / top) * (100 - MIN_BAR) : 0;
        bar.style.width = pct.toFixed(2) + '%';
        row.classList.toggle('zero', values[i] === 0);
      });
      if (note) {
        if (!alive) {
          note.textContent =
            'Сервис на связи и ждёт первую кампанию — воронка заполнится с первым запуском.';
        } else if (values[3] > 0) {
          note.textContent = 'Каждый ответ прилетает владельцу кампании в Telegram.';
        } else {
          note.textContent = 'Считаем воронку по всему сервису с первого дня.';
        }
      }
    }

    /* Счётчики дорисовываются твином из landing.js, поэтому перекрашиваем
       полосы несколько раз, пока анимация чисел не закончится. */
    var ticks = 0;
    var timer = setInterval(function () {
      paint();
      if (++ticks > 30) clearInterval(timer);
    }, 200);
    paint();
  })();

  /* Демо-окно на узком экране: настоящая десктопная композиция, просто
     уменьшенная (Samat). Габариты МЕРЯЕМ, а не хардкодим: transform не влияет
     на layout, поэтому offsetWidth/Height у рамки всегда натуральные — и
     после смены вёрстки демо ничего не разъезжает. В CSS так нельзя:
     длину на длину там не поделить. */
  (function () {
    var wrap = document.querySelector('.appwin-scale');
    if (!wrap) return;
    var frame = wrap.querySelector('.aw-frame');
    if (!frame) return;
    // Ниже этого масштаба подписи в демо превращаются в кашу: на телефоне
    // ужимать окно до ширины экрана бессмысленно — вместо этого оставляем
    // читаемый масштаб и даём листать вбок.
    var MIN_K = 0.58;
    function fit() {
      var avail = wrap.clientWidth,
        natural = frame.offsetWidth,
        height = frame.offsetHeight;
      if (!natural || avail >= natural) {
        wrap.classList.remove('is-scrollable');
        wrap.style.removeProperty('--aw-k');
        wrap.style.removeProperty('--aw-trim');
        wrap.style.removeProperty('height');
        return;
      }
      var k = avail / natural;
      var scrollable = k < MIN_K;
      if (scrollable) k = MIN_K;
      wrap.classList.toggle('is-scrollable', scrollable);
      wrap.style.setProperty('--aw-k', String(k));
      // transform не влияет на поток, поэтому ширина рамки осталась бы
      // натуральной и полоса прокрутки уезжала бы в пустоту. Подрезаем
      // margin-ом ровно на то, что съел масштаб.
      wrap.style.setProperty('--aw-trim', Math.round(natural * (1 - k)) + 'px');
      wrap.style.height = Math.round(height * k) + 'px';
    }
    fit();
    window.addEventListener('resize', fit);
    window.addEventListener('load', fit);
  })();

  /* telegram chat loop */
  (function () {
    var chat = document.getElementById('chat'),
      sub = document.getElementById('tgSub');
    if (!chat) return;
    function tm(t) {
      return '<span class="tm">' + t + '</span>';
    }
    function tmc(t) {
      return '<span class="tm">' + t + ' <span class="chk">✓✓</span></span>';
    }
    /* Один исходящий с одной картинкой-превью и один ответ (Samat:
       «сообщение сделай 1 и фотку 1»). Превью — не фотосток, а рисованная
       карточка оффера: лёгкая (inline svg), чёткая на любом экране. */
    var PHOTO =
      '<span class="tg-photo">' +
      '<svg viewBox="0 0 320 180" role="img" aria-label="Превью оффера">' +
      '<defs><linearGradient id="tgPhG" x1="0" y1="1" x2="1" y2="0">' +
      '<stop offset="0" stop-color="#c22bff"/><stop offset=".55" stop-color="#ff2fa4"/>' +
      '<stop offset="1" stop-color="#ff8fe0"/></linearGradient></defs>' +
      '<rect width="320" height="180" fill="#0d0b12"/>' +
      '<circle cx="266" cy="34" r="52" fill="url(#tgPhG)" opacity=".22"/>' +
      '<path d="M20 132 68 104l34 16 40-38 36 20 42-52 40 22" fill="none" stroke="url(#tgPhG)" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<circle cx="142" cy="82" r="4.6" fill="#ff8fe0"/><circle cx="220" cy="52" r="4.6" fill="#ff8fe0"/>' +
      '<text x="20" y="42" fill="#f5eef6" font-family="monospace" font-size="15" font-weight="700">Аналитика портфеля</text>' +
      '<text x="20" y="63" fill="#a99cad" font-family="monospace" font-size="11">интеграция в ролик · медиакит</text>' +
      '<rect x="20" y="146" width="96" height="20" rx="10" fill="url(#tgPhG)"/>' +
      '<text x="34" y="160" fill="#0d0b12" font-family="monospace" font-size="10" font-weight="700">МЕДИАКИТ</text>' +
      '</svg></span>';
    var SEQ = [
      { c: 'tg-date', t: 'Сегодня', d: 500, html: 'Сегодня' },
      {
        c: 'tg-out',
        d: 1400,
        html:
          PHOTO +
          '<span class="tg-txt">Привет! Смотрел ваш разбор портфеля на 2026 — зашла часть про облигации. Собрали медиакит под ваш канал: интеграция, бюджет обсуждаем. Интересно?</span>' +
          tmc('14:02'),
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
    var run = false;
    var notice = document.getElementById('phNotice');
    function showNotice() {
      if (!notice) return;
      notice.classList.add('on');
      notice.setAttribute('aria-hidden', 'false');
    }
    function hideNotice() {
      if (!notice) return;
      notice.classList.remove('on');
      notice.setAttribute('aria-hidden', 'true');
    }
    function play(i) {
      if (i >= SEQ.length) {
        setTimeout(function () {
          chat.innerHTML = '';
          hideNotice();
          play(0);
        }, 5600);
        return;
      }
      var m = SEQ[i];
      setTimeout(function () {
        if (m.notice) {
          // Пуш от бота поверх экрана: внутри переписки такого сообщения не
          // бывает — уведомление о ответе присылает Talaria, а не собеседник.
          showNotice();
          play(i + 1);
        } else if (m.typing) {
          sub.textContent = 'печатает…';
          sub.classList.add('typ');
          var tp = document.createElement('div');
          tp.className = 'tg-msg tg-in show';
          tp.innerHTML =
            '<span class="tg-typing"><i style="--td:0s"></i><i style="--td:.15s"></i><i style="--td:.3s"></i></span>';
          chat.appendChild(tp);
          setTimeout(function () {
            tp.remove();
            sub.textContent = 'в сети';
            sub.classList.remove('typ');
            add(m);
            play(i + 1);
          }, 1600);
        } else {
          add(m);
          play(i + 1);
        }
      }, m.d);
    }
    function add(m) {
      var el = document.createElement('div');
      el.className = m.c === 'tg-date' || m.c === 'tg-sys' ? m.c : 'tg-msg ' + m.c;
      el.innerHTML = m.html;
      chat.appendChild(el);
      if (el.classList.contains('tg-msg'))
        requestAnimationFrame(function () {
          requestAnimationFrame(function () {
            el.classList.add('show');
          });
        });
      while (chat.children.length > 7) chat.removeChild(chat.firstChild);
    }
    new IntersectionObserver(
      function (es) {
        if (es[0].isIntersecting && !run) {
          run = true;
          play(0);
        }
      },
      { threshold: 0.3 },
    ).observe(chat);
  })();

  /* count-up */
  (function () {
    var io = new IntersectionObserver(
      function (es) {
        es.forEach(function (e) {
          if (!e.isIntersecting) return;
          io.unobserve(e.target);
          var el = e.target,
            to = +el.dataset.cnt,
            suf = el.dataset.suf || '',
            t0 = null;
          function fr(ts) {
            if (!t0) t0 = ts;
            var k = Math.min(1, (ts - t0) / 1400);
            k = 1 - Math.pow(1 - k, 3);
            el.textContent =
              Math.round(to * k).toLocaleString('ru-RU') + (k === 1 && suf ? ' ' + suf : '');
            if (k < 1) requestAnimationFrame(fr);
          }
          requestAnimationFrame(fr);
        });
      },
      { threshold: 0.5 },
    );
    document.querySelectorAll('[data-cnt]').forEach(function (el) {
      io.observe(el);
    });
  })();

  /* Тариф рисует tariffs.js в #landingTariffGrid: цены и промокод приходят
  /* ДЕМО-ОКНО «Полёт кампании».
     Собрано из настоящих классов кабинета (.app/.sidebar/.nav-item/.topbar/
     .panel/table) с демо-данными, поэтому здесь остаётся только сценарий:
     переключение страниц по кругу и курсор, который «кликает» по меню.
     Крутится только когда окно видно — иначе таймеры зря греют телефон. */
  (function () {
    var win = document.querySelector('.appwin');
    if (!win) return;
    var curEl = win.querySelector('.awcur'),
      navs = [].slice.call(win.querySelectorAll('.nav-item[data-s]')),
      segs = [].slice.call(
        (win.closest('.aw-frame') || document).querySelectorAll('.aw-seg[data-s]'),
      ),
      scenes = [].slice.call(win.querySelectorAll('.aw-scene')),
      order = [1, 2, 3],
      at = 0,
      timer = null,
      cursorTimers = [];

    function show(step) {
      scenes.forEach(function (s) {
        s.classList.toggle('on', s.getAttribute('data-s') === String(step));
      });
      navs.forEach(function (n) {
        n.classList.toggle('active', n.getAttribute('data-s') === String(step));
      });
      segs.forEach(function (sg) {
        sg.classList.toggle('on', sg.getAttribute('data-s') === String(step));
      });
    }

    /* Курсор ходит по элементам текущей страницы и «щёлкает» по пункту меню
       перед сменой сцены — так видно, что окно живое, а не картинка. */
    function moveTo(el, click, delay) {
      cursorTimers.push(
        setTimeout(function () {
          if (!el) return;
          var wr = win.getBoundingClientRect(),
            r = el.getBoundingClientRect();
          curEl.style.left = (((r.left + r.width / 2 - wr.left) / wr.width) * 100).toFixed(2) + '%';
          curEl.style.top = (((r.top + r.height / 2 - wr.top) / wr.height) * 100).toFixed(2) + '%';
          if (click) {
            curEl.classList.remove('clk');
            void curEl.offsetWidth;
            curEl.classList.add('clk');
          }
        }, delay),
      );
    }

    function choreograph(step, nextStep) {
      cursorTimers.forEach(clearTimeout);
      cursorTimers = [];
      var scene = win.querySelector('.aw-scene[data-s="' + step + '"]');
      if (!scene) return;
      var stops = [].slice
        .call(scene.querySelectorAll('.chip, .stat-card, tbody tr, .offer-foot .btn, .run-bar'))
        .slice(0, 3);
      stops.forEach(function (el, i) {
        moveTo(el, i === 0, 500 + i * 1400);
      });
      var nextNav = win.querySelector('.nav-item[data-s="' + nextStep + '"]');
      moveTo(nextNav, true, 4700);
    }

    function tick() {
      at = (at + 1) % order.length;
      var step = order[at];
      show(step);
      choreograph(step, order[(at + 1) % order.length]);
    }

    function start() {
      if (timer) return;
      choreograph(order[at], order[(at + 1) % order.length]);
      timer = setInterval(tick, 6000);
    }
    function stop() {
      clearInterval(timer);
      timer = null;
      cursorTimers.forEach(clearTimeout);
      cursorTimers = [];
    }

    new IntersectionObserver(
      function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting) start();
          else stop();
        });
      },
      { threshold: 0.25 },
    ).observe(win);

    /* Клик по пункту меню в демо — ручное переключение страницы. */
    navs.forEach(function (n) {
      n.addEventListener('click', function () {
        var step = Number(n.getAttribute('data-s'));
        var idx = order.indexOf(step);
        if (idx < 0) return;
        at = idx;
        show(step);
        choreograph(step, order[(at + 1) % order.length]);
      });
    });
  })();

  /* buttery smooth wheel scroll (desktop, lenis-style) */
  (function () {
    if (matchMedia('(pointer:coarse)').matches) return;
    document.documentElement.style.scrollBehavior = 'auto';
    var target = scrollY,
      cur = scrollY,
      raf = null;
    function mx() {
      return document.documentElement.scrollHeight - innerHeight;
    }
    function step() {
      cur += (target - cur) * 0.075;
      if (Math.abs(target - cur) < 0.6) {
        cur = target;
        raf = null;
      } else raf = requestAnimationFrame(step);
      scrollTo(0, cur);
    }
    function go() {
      if (!raf) raf = requestAnimationFrame(step);
    }
    addEventListener(
      'wheel',
      function (e) {
        if (e.ctrlKey || __landingHidden()) return;
        e.preventDefault();
        var d = e.deltaY * (e.deltaMode === 1 ? 16 : 1);
        target = Math.max(0, Math.min(mx(), target + d));
        go();
      },
      { passive: false },
    );
    addEventListener(
      'scroll',
      function () {
        if (!raf) {
          target = cur = scrollY;
        }
      },
      { passive: true },
    );
    window.__scrollTo = function (y) {
      target = Math.max(0, Math.min(mx(), y));
      go();
    };
    document.addEventListener('click', function (e) {
      if (__landingHidden()) return;
      var a = e.target.closest ? e.target.closest('a[href^="#"]') : null;
      if (!a || a.hasAttribute('data-telegram-login-trigger')) return;
      var el = document.querySelector(a.getAttribute('href'));
      if (!el) return;
      e.preventDefault();
      window.__scrollTo(el.getBoundingClientRect().top + scrollY - 70);
    });
  })();

  /* Финал: скролл-скраба здесь больше нет. Правка Samat — «текст поднимается,
     убери это»: вордмарк за рукой просто стоит на месте, а движение внизу
     страницы делает шторка (розовая секция тарифа уезжает вверх, открывая
     сцену) — это чистый CSS, без пересчёта на каждый кадр прокрутки. */

  /* v48: FAQ-переписка. Клик по вопросу-пузырю открывает ответ от Talaria.
     Несколько веток могут быть открыты одновременно — как в настоящем чате. */
  (function () {
    var chat = document.getElementById('faqChat');
    if (!chat) return;
    var noWait = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    chat.addEventListener('click', function (e) {
      var q = e.target.closest ? e.target.closest('.faq-q') : null;
      if (!q) return;
      var item = q.closest('.faq-item');
      if (!item) return;
      if (item.classList.contains('open')) {
        item.classList.remove('open', 'typing');
        q.setAttribute('aria-expanded', 'false');
        return;
      }
      if (item.classList.contains('typing')) return;
      q.setAttribute('aria-expanded', 'true');
      /* Сначала «печатает…» — ответ приходит, как от живого собеседника.
         С reduced-motion пузырь открывается сразу. */
      if (noWait) {
        item.classList.add('open');
        return;
      }
      item.classList.add('typing');
      setTimeout(function () {
        if (!item.classList.contains('typing')) return;
        item.classList.remove('typing');
        item.classList.add('open');
      }, 650);
    });
  })();

  /* Терминал героя: полёт письма как процесс. Строки появляются по одной,
     команда печатается посимвольно; цикл бесконечный. Содержимое честное:
     те же цифры, что в секциях ниже (скоринг 92, ответ за 7 минут). */
  (function () {
    var body = document.getElementById('heroTerm');
    if (!body) return;
    var reduced =
      window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var LINES = [
      { c: 'ht-cmd', t: 'talaria fly --ниша финансы --оффер медиакит', type: true },
      { c: 'ht-dim', t: 'скан YouTube: ru · активность 30 дней', d: 700 },
      { c: 'ht-ok', t: '✓ Инвестируй Просто · 142 тыс. · скоринг 92', d: 900 },
      { c: 'ht-dim', t: 'контекст: «Разбор портфеля на 2026»', d: 800 },
      { c: 'ht-dim', t: 'вступление собрано — 2 предложения, не шаблон', d: 900 },
      { c: 'ht-ok', t: '✓ доставлено с @maria_work · 14:02 ✓✓', d: 1100 },
      { c: 'ht-reply', t: '← ответ через 7 мин: «Да, звучит интересно»', d: 1600 },
    ];
    function addLine(spec) {
      var el = document.createElement('div');
      el.className = 'ht-line ' + spec.c;
      el.textContent = spec.t;
      body.appendChild(el);
      return el;
    }
    if (reduced) {
      LINES.forEach(addLine);
      return;
    }
    var timer = 0;
    function restart() {
      timer = setTimeout(function () {
        body.innerHTML = '';
        play(0);
      }, 5200);
    }
    function typeCmd(spec, next) {
      var el = document.createElement('div');
      el.className = 'ht-line ht-caret ' + spec.c;
      el.textContent = '';
      body.appendChild(el);
      var i = 0;
      (function tick() {
        if (i <= spec.t.length) {
          el.textContent = spec.t.slice(0, i);
          i += 2;
          timer = setTimeout(tick, 34);
        } else {
          el.classList.remove('ht-caret');
          next();
        }
      })();
    }
    function play(i) {
      if (i >= LINES.length) return restart();
      var spec = LINES[i];
      if (spec.type) return typeCmd(spec, play.bind(null, i + 1));
      timer = setTimeout(function () {
        addLine(spec);
        play(i + 1);
      }, spec.d || 600);
    }
    play(0);
  })();

  /* Промо-модалка убрана: код скидки не публикуется в разметке, промокод
     вводится в карточке тарифа и проверяется на сервере. */
})();
