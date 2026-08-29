(() => {
  const params = new URLSearchParams(location.search);
  const ticket = params.get('t') || '';
  document.documentElement.dataset.theme = params.get('theme') === 'light' ? 'light' : 'dark';
  const ui = {
    form: document.getElementById('form'), phone: document.getElementById('phone'),
    code: document.getElementById('code'), password: document.getElementById('password'),
    phoneField: document.getElementById('phoneField'), codeField: document.getElementById('codeField'),
    passwordField: document.getElementById('passwordField'), qrField: document.getElementById('qrField'),
    qrImage: document.getElementById('qrImage'), submit: document.getElementById('submit'),
    resend: document.getElementById('resend'), qrLogin: document.getElementById('qrLogin'),
    authDivider: document.getElementById('authDivider'), status: document.getElementById('status'),
  };
  let ws = null;
  let stage = 'phone';
  let resendTimer = null;
  const stopResendTimer = () => { if (resendTimer) clearInterval(resendTimer); resendTimer = null; };
  const startResendTimer = () => {
    stopResendTimer();
    let seconds = 30;
    ui.resend.classList.remove('hidden');
    ui.resend.disabled = true;
    ui.resend.textContent = `Повторить через ${seconds} сек`;
    resendTimer = setInterval(() => {
      seconds -= 1;
      if (seconds <= 0) {
        stopResendTimer();
        ui.resend.disabled = false;
        ui.resend.textContent = 'Отправить код повторно';
      } else ui.resend.textContent = `Повторить через ${seconds} сек`;
    }, 1000);
  };
  const setStatus = (text, cls = '') => { ui.status.textContent = text || ''; ui.status.className = `status ${cls}`; };
  const setStage = (next) => {
    stage = next;
    ui.phoneField.classList.toggle('hidden', next !== 'phone');
    ui.codeField.classList.toggle('hidden', next !== 'code');
    ui.passwordField.classList.toggle('hidden', next !== 'password');
    ui.qrField.classList.toggle('hidden', next !== 'qr');
    ui.resend.classList.toggle('hidden', next !== 'code');
    ui.qrLogin.classList.toggle('hidden', !['phone', 'code'].includes(next));
    ui.authDivider.classList.toggle('hidden', next !== 'phone');
    ui.submit.classList.toggle('hidden', next === 'qr');
    ui.submit.classList.toggle('secondary', next === 'phone');
    ui.qrLogin.disabled = false;
    if (next !== 'code') stopResendTimer();
    ui.submit.textContent = next === 'phone' ? 'Получить код' : next === 'code' ? 'Подтвердить код' : 'Подтвердить пароль';
    ui.submit.disabled = false;
    if (next === 'code') ui.code.focus();
    if (next === 'password') ui.password.focus();
  };
  // Высота iframe считается по содержимому: форма меняет высоту на каждом шаге
  // (номер → код → 2FA → QR), а фиксированная высота давала полосы прокрутки
  // внутри модалки. Сообщение слушает frontend/js/settings.js.
  const card = document.querySelector('.card');
  const reportHeight = () => {
    if (!card) return;
    const height = Math.ceil(card.getBoundingClientRect().height);
    try { window.parent.postMessage({ type: 'mf:tg-height', height }, location.origin); } catch (_) {}
  };
  if (window.self !== window.top && card) {
    new ResizeObserver(reportHeight).observe(card);
    window.addEventListener('load', reportHeight);
    reportHeight();
  }

  const send = (data) => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(data)); };
  const connected = (me) => {
    const label = me && (me.username ? `@${me.username}` : me.name);
    setStatus(`Подключено${label ? `: ${label}` : ''}`, 'ok');
    ui.form.classList.add('hidden');
    try { window.parent.postMessage({ type: 'mf:tg-account-connected' }, location.origin); } catch (_) {}
  };
  const handle = (data) => {
    if (data.type === 'session_status') {
      if (data.hasSession) setStatus('Проверяю сохранённую сессию…');
      else { setStatus('Введите номер аккаунта'); setStage('phone'); }
    } else if (data.type === 'status') setStatus(data.status || 'Подключаюсь…');
    else if (data.type === 'need_code') {
      const wasCodeStage = stage === 'code';
      setStatus(data.delivery === 'sms'
        ? 'Код отправлен по SMS.'
        : 'Telegram выбрал доставку в приложение. Проверьте служебный чат «Telegram» или используйте QR-вход без кода.');
      setStage('code');
      if (!wasCodeStage || data.resent) startResendTimer();
    }
    else if (data.type === 'need_password') { setStatus('Нужен пароль двухэтапной аутентификации'); setStage('password'); }
    else if (data.type === 'qr_code') {
      if (typeof data.image !== 'string' || !data.image.startsWith('data:image/png;base64,')) return;
      ui.qrImage.src = data.image;
      setStage('qr');
      setStatus('QR обновляется автоматически. Подтвердите вход в Telegram на телефоне.');
    }
    else if (data.type === 'me') window.__telegramMe = data.me;
    else if (data.type === 'logged_in') connected(window.__telegramMe || null);
    else if (data.type === 'error') { setStatus(data.error || 'Ошибка подключения', 'error'); ui.submit.disabled = false; ui.qrLogin.disabled = false; }
  };
  ui.form.addEventListener('submit', (event) => {
    event.preventDefault(); ui.submit.disabled = true;
    if (stage === 'phone') {
      const phone = ui.phone.value.trim().replace(/[()\s-]/g, '');
      if (!/^\+\d{8,15}$/.test(phone)) { setStatus('Введите номер в формате +77001234567', 'error'); ui.submit.disabled = false; return; }
      setStatus('Запрашиваю код…'); send({ type: 'login_start', phone });
    } else if (stage === 'code') {
      const code = ui.code.value.trim();
      if (!/^\d{4,8}$/.test(code)) { setStatus('Введите код из 4–8 цифр', 'error'); ui.submit.disabled = false; return; }
      send({ type: 'code', code });
    }
    else send({ type: 'password', password: ui.password.value });
  });
  ui.resend.addEventListener('click', () => {
    if (stage !== 'code' || ui.resend.disabled) return;
    ui.resend.disabled = true;
    setStatus('Запрашиваю другой способ доставки…');
    send({ type: 'resend' });
  });
  ui.qrLogin.addEventListener('click', () => {
    ui.qrLogin.disabled = true;
    setStatus('Создаю безопасный QR-код…');
    send({ type: 'login_qr' });
  });
  window.addEventListener('message', event => {
    if (event.origin !== location.origin) return;
    if (event.data && event.data.type === 'mf:theme') document.documentElement.dataset.theme = event.data.theme === 'light' ? 'light' : 'dark';
  });
  if (!ticket) { setStatus('Билет подключения отсутствует. Откройте настройки заново.', 'error'); ui.submit.disabled = true; return; }
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const base = location.pathname.replace(/[^/]*$/, '');
  ws = new WebSocket(`${proto}//${location.host}${base}?t=${encodeURIComponent(ticket)}`);
  ws.addEventListener('message', event => { try { handle(JSON.parse(event.data)); } catch (_) {} });
  ws.addEventListener('close', () => { if (!ui.form.classList.contains('hidden')) setStatus('Соединение закрыто. Откройте настройки заново.', 'error'); });
  ws.addEventListener('error', () => setStatus('Telegram-gateway недоступен', 'error'));
})();
