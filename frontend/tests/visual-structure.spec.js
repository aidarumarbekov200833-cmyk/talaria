import { expect, test } from '@playwright/test';
import { mockApi } from './helpers/mock-api.js';
test.setTimeout(120_000);

const landingMatrix = [
  ['landing-desktop', 1440, 1000],
  ['landing-tablet', 1024, 768],
  ['landing-mobile', 390, 844],
];
const sections = [
  'dashboard',
  'search',
  'databases',
  'offer',
  'mailing',
  'messages',
  'tariffs',
  'settings',
  'admin',
];
const sectionSentinels = {
  dashboard: '#offerStatsPanel',
  search: '.search-filter-panel',
  databases: '#dbGridView',
  offer: '#offerPaneManual',
  mailing: '#campaignNameInput',
  messages: '#chatDialogs',
  tariffs: '#tariffGrid [data-plan-card]',
  settings: '#accountHealthCenter',
  admin: '#adminOverview',
};

function pngDimensions(png) {
  return { width: png.readUInt32BE(16), height: png.readUInt32BE(20) };
}

async function expectNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => {
    const viewport = window.innerWidth;
    const elements = [...document.querySelectorAll('body *')]
      .filter((element) => {
        // Финальная сцена — намеренно полноэкранная и обрезается по краям
        // (overflow:hidden): рука выезжает из-за правого края экрана. Реальную
        // ширину документа это не меняет, поэтому клипованный слой пропускаем.
        if (element.closest('.zp-final-sticky')) return false;
        // v39. Демо кабинета на узком экране больше не листается вбок — оно
        // вписано целиком. Исключение оставлено только на случай очень узких
        // экранов, где масштаб дошёл бы до нечитаемого.
        if (element.closest('.appwin-scale.is-scrollable')) return false;
        const rect = element.getBoundingClientRect();
        return rect.right > viewport + 1 || rect.left < -1;
      })
      .slice(0, 8)
      .map((element) => ({
        className: element.className,
        id: element.id,
        tagName: element.tagName,
        text: element.textContent?.trim().slice(0, 80),
      }));
    return {
      documentWidth: document.documentElement.scrollWidth,
      viewport,
      elements,
    };
  });
  expect(overflow.documentWidth).toBeLessThanOrEqual(overflow.viewport);
  expect(overflow.elements).toEqual([]);
}

async function hideNonCaptureLayers(page) {
  await page
    .locator('.drawer, .modal-overlay, .onb-overlay, .overlay-soft, .toast-wrap')
    .evaluateAll((elements) => {
      for (const element of elements) {
        element.classList.remove('show');
        element.style.display = 'none';
      }
    });
}

/** Погасить только тур и подсказки: модалки в тестах v40 нужны рабочими. */
async function hideOnboardingOnly(page) {
  await page.locator('.onb-overlay, .toast-wrap, .drawer').evaluateAll((elements) => {
    for (const element of elements) {
      element.classList.remove('show');
      element.style.display = 'none';
    }
  });
}

async function prepareFullPageLandingCapture(page) {
  await page.evaluate(() => {
    document.documentElement.style.overflow = 'visible';
    document.body.style.overflow = 'visible';
    Object.assign(document.querySelector('#loginScreen').style, {
      position: 'relative',
      inset: 'auto',
      overflow: 'visible',
      height: 'auto',
    });
  });
  const landing = await page.evaluate(() => ({
    viewportHeight: window.innerHeight,
    documentHeight: document.documentElement.scrollHeight,
    finaleTop: document.querySelector('#zephyrFinale').getBoundingClientRect().top,
  }));
  expect(landing.documentHeight).toBeGreaterThan(landing.viewportHeight);
  expect(landing.finaleTop).toBeGreaterThan(landing.viewportHeight);
}

async function activateCaptureSection(page, sectionId) {
  await page.evaluate((activeId) => {
    for (const element of document.querySelectorAll('[data-target]')) {
      const active = element.getAttribute('data-target') === activeId;
      element.classList.toggle('active', active);
    }
    for (const element of document.querySelectorAll('.section')) {
      const active = element.id === activeId;
      element.classList.toggle('active', active);
      element.hidden = !active;
      element.style.display = active ? 'block' : 'none';
    }
    window.scrollTo(0, 0);
  }, sectionId);
}

test('captures the required landing and workspace visual matrix without overflow', async ({
  page,
}, testInfo) => {
  await mockApi(page);

  for (const [name, width, height] of landingMatrix) {
    await page.setViewportSize({ width, height });
    for (const theme of ['light', 'dark']) {
      await page.goto('/index.html');
      await page
        .locator('html')
        .evaluate((element, value) => element.setAttribute('data-theme', value), theme);
      await page.locator('#loginHint').evaluate((element) => {
        element.style.display = 'none';
      });
      await hideNonCaptureLayers(page);
      // Композиция лендинга V5: шесть возможностей, три шага кампании и три
      // гарантии доставки. Прежние .zp-product-card из старого лендинга ушли
      // вместе с ним — фиксируем то, что на странице есть сейчас.
      await expect(page.locator('.zp-capabilities .fcard')).toHaveCount(6);
      await expect(page.locator('.zp-workflow li')).toHaveCount(3);
      await expect(page.locator('.zp-account-band .ab-list li')).toHaveCount(3);
      await prepareFullPageLandingCapture(page);
      await expect(page.locator('.zp-final')).toBeVisible();
      // Финал переработан в шторку: подписи по углам убраны (Samat), слово
      // TALARIA на шторке остаётся ключевым элементом кадра.
      await expect(page.locator('.zp-final .fin-word')).toBeVisible();
      await expectNoHorizontalOverflow(page);
      const screenshot = await page.screenshot({
        path: testInfo.outputPath(`${name}-${theme}.png`),
        fullPage: true,
      });
      const dimensions = pngDimensions(screenshot);
      expect(dimensions.width).toBe(width);
      expect(dimensions.height).toBeGreaterThan(height);
    }
  }

  for (const [viewportName, width, height] of [
    ['workspace-desktop', 1440, 1000],
    ['workspace-mobile', 390, 844],
  ]) {
    await page.setViewportSize({ width, height });
    for (const theme of ['light', 'dark']) {
      await page.addInitScript(() => {
        localStorage.setItem('mf_token', 'visual-fixture-token');
        // v38. Признак «кабинет уже открывали в этой вкладке» живёт в
        // sessionStorage и заставляет страницу входить сама (это и есть новое
        // поведение при F5). В тесте каждый прогон начинается с лендинга, поэтому
        // отметку снимаем — иначе кнопка «Войти» уже скрыта и клик невозможен.
        sessionStorage.removeItem('talaria_in_app');
      });
      await page.goto('/index.html');
      // Автовхода больше нет (по просьбе владельца): живой токен оставляет
      // человека на лендинге. В кабинет заходим так же, как заходит он —
      // кликом по «Войти». Признак data-session="warm" появляется, когда
      // токен проверен и вход пройдёт мгновенно, без окна Telegram.
      await expect(page.locator('html[data-session="warm"]')).toHaveCount(1);
      await page.locator('[data-telegram-login-trigger]').first().click();
      await expect(page.locator('#appWrapper')).toHaveClass(/\bactive\b/);
      await page
        .locator('html')
        .evaluate((element, value) => element.setAttribute('data-theme', value), theme);
      await page.locator('#loginScreen').evaluate((element) => {
        element.classList.add('hidden');
        element.style.display = 'none';
      });
      await page.locator('#appWrapper').evaluate((element) => {
        element.style.display = 'block';
      });
      await expect(page.locator('#loginScreen')).toHaveCSS('display', 'none');
      await hideNonCaptureLayers(page);
      for (const sectionId of sections) {
        await activateCaptureSection(page, sectionId);
        await expect(page.locator(`#${sectionId}`)).toBeVisible();
        await expect(page.locator(`#${sectionId}`)).toHaveClass(/\bactive\b/);
        await expect(page.locator(`#${sectionId} .page-title`)).toBeVisible();
        expect(
          await page
            .locator(`[data-target="${sectionId}"].active`)
            .evaluateAll(
              (elements, activeId) =>
                elements.length > 0 &&
                [...document.querySelectorAll('[data-target].active')].every(
                  (element) => element.getAttribute('data-target') === activeId,
                ),
              sectionId,
            ),
        ).toBe(true);
        await page.waitForTimeout(550);
        await activateCaptureSection(page, sectionId);
        await expect(page.locator(`#${sectionId}`)).toHaveCSS('opacity', '1');
        await expect(page.locator(`#${sectionId}`)).toHaveCSS('display', 'block');
        const sectionBox = await page.locator(`#${sectionId}`).boundingBox();
        expect(sectionBox?.height || 0).toBeGreaterThan(100);
        expect(sectionBox?.y ?? 1000).toBeLessThan(1000);
        await expect(page.locator(sectionSentinels[sectionId])).toBeVisible();
        if (sectionId === 'tariffs') {
          await expect(page.locator('#tariffGrid [data-plan-card]')).toBeVisible();
        }
        if (theme === 'dark') {
          await expect(page.locator(`#${sectionId} .page-title`)).toHaveCSS(
            'color',
            'rgb(245, 238, 246)',
          );
        }
        await expectNoHorizontalOverflow(page);
        await page.screenshot({
          path: testInfo.outputPath(`${viewportName}-${sectionId}-${theme}.png`),
        });
      }
    }
  }
});

test('setup tasks live inside the notifications dropdown with an unread badge', async ({
  page,
}) => {
  await mockApi(page);
  for (const [width, height] of [
    [1440, 1000],
    [390, 844],
  ]) {
    await page.setViewportSize({ width, height });
    await page.addInitScript(() => {
      localStorage.setItem('mf_token', 'visual-fixture-token');
      // Отметка «прочитано» живёт в localStorage: на втором прогоне (другой
      // экран, тот же контекст) без сброса непрочитанных уже не будет.
      localStorage.removeItem('talaria_notif_seen');
      sessionStorage.removeItem('talaria_in_app');
    });
    await page.goto('/index.html');
    // Автовхода нет: в кабинет заходим кликом по «Войти», как живой человек.
    await expect(page.locator('html[data-session="warm"]')).toHaveCount(1);
    await page.locator('[data-telegram-login-trigger]').first().click();
    await expect(page.locator('#appWrapper')).toHaveClass(/\bactive\b/);
    await page.locator('#loginScreen').evaluate((element) => {
      element.classList.add('hidden');
      element.style.display = 'none';
    });
    await page.locator('#appWrapper').evaluate((element) => {
      element.style.display = 'block';
    });
    await hideNonCaptureLayers(page);
    await activateCaptureSection(page, 'dashboard');

    // Плиток поверх страницы больше нет: подсказки настройки ушли в колокольчик.
    await expect(page.locator('#setupNotices')).toBeHidden();
    // Пока список не открыли, точка на колокольчике мигает и показывает счёт.
    await expect(page.locator('#notifPing')).toHaveClass(/\bis-on\b/);
    await expect(page.locator('#notifPing')).toHaveText('2');

    await page.locator('[data-args=\'["ddNotif"]\']').click();
    await expect(page.locator('#ddNotif')).toHaveClass(/\bshow\b/);
    await expect(page.locator('#ddNotif .dd-setup-item')).toHaveCount(2);
    await expect(page.locator('#notifCount')).toHaveText('2 новых');
    // v38. Задачи настройки открытием списка не гасятся: пока ключа YouTube и
    // Telegram-аккаунта нет, работать нечем, и точка обязана мигать (просьба
    // владельца). Гаснут только обычные события — их прочтение отмечается.
    await expect(page.locator('#notifPing')).toHaveClass(/\bis-on\b/);
    await expect(page.locator('#notifPing')).toHaveText('2');

    const geometry = await page.evaluate(() => {
      const dropdown = document.querySelector('#ddNotif').getBoundingClientRect();
      return {
        insideViewport:
          dropdown.left >= -1 &&
          dropdown.right <= window.innerWidth + 1 &&
          dropdown.bottom <= window.innerHeight + 1,
        hasHeight: dropdown.height > 80,
      };
    });
    expect(geometry).toEqual({ insideViewport: true, hasHeight: true });
    await expectNoHorizontalOverflow(page);

    // Клик по подсказке ведёт в настройки и закрывает список за собой.
    await page.locator('#ddNotif .dd-setup-item').first().click();
    await expect(page.locator('#ddNotif')).not.toHaveClass(/\bshow\b/);
  }
});

test('ai chat keeps the composer on screen and scrolls its message feed', async ({ page }) => {
  await mockApi(page);
  for (const [width, height] of [
    [1440, 900],
    [390, 844],
  ]) {
    await page.setViewportSize({ width, height });
    await page.addInitScript(() => {
      localStorage.setItem('mf_token', 'visual-fixture-token');
      sessionStorage.removeItem('talaria_in_app');
    });
    await page.goto('/index.html');
    await page.locator('[data-telegram-login-trigger]').first().click();
    await expect(page.locator('#appWrapper')).toHaveClass(/\bactive\b/);
    await page.locator('#loginScreen').evaluate((element) => {
      element.classList.add('hidden');
      element.style.display = 'none';
    });
    await page.locator('#appWrapper').evaluate((element) => {
      element.style.display = 'block';
    });
    await activateCaptureSection(page, 'dashboard');
    await page.locator('.icon-btn.ai').click();
    await expect(page.locator('#aiDrawer')).toHaveClass(/\bshow\b/);

    // Длинная переписка не должна выдавливать поле ввода за нижний край.
    await page.evaluate(() => {
      const body = document.getElementById('aiBody');
      for (let index = 0; index < 12; index += 1) {
        const bubble = document.createElement('div');
        bubble.className = index % 2 ? 'msg ai' : 'msg user';
        bubble.textContent = `Сообщение ${index + 1}: длинная строка, чтобы лента переросла высоту ящика.`;
        body.appendChild(bubble);
      }
      body.scrollTop = body.scrollHeight;
    });

    const layout = await page.evaluate(() => {
      const body = document.getElementById('aiBody');
      const input = document.querySelector('#aiDrawer .drawer-input');
      const inputRect = input.getBoundingClientRect();
      const bodyRect = body.getBoundingClientRect();
      return {
        composerVisible: inputRect.bottom <= window.innerHeight + 1 && inputRect.height > 20,
        composerBelowFeed: inputRect.top >= bodyRect.bottom - 1,
        feedScrolls: body.scrollHeight > body.clientHeight + 1,
        feedStaysInsideDrawer: bodyRect.bottom <= window.innerHeight + 1,
        scrolledToBottom: Math.abs(body.scrollHeight - body.clientHeight - body.scrollTop) < 2,
      };
    });
    expect(layout).toEqual({
      composerVisible: true,
      composerBelowFeed: true,
      feedScrolls: true,
      feedStaysInsideDrawer: true,
      scrolledToBottom: true,
    });
  }
});

test('v39: демо кабинета видно целиком и в палитре кабинета, а не лендинга', async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.removeItem('talaria_in_app');
  });
  for (const [width, height] of [
    [390, 844],
    [1440, 1000],
  ]) {
    await page.setViewportSize({ width, height });
    await page.goto('/index.html');
    await page.locator('.appwin-scale').scrollIntoViewIfNeeded();
    const demo = await page.evaluate(() => {
      const wrap = document.querySelector('.appwin-scale');
      const frame = document.querySelector('.aw-frame');
      const cta = document.querySelector('#loginScreen .aw-app .chip.accent');
      const styles = getComputedStyle(frame);
      const asRgb = (value) => {
        const probe = document.createElement('span');
        probe.style.color = value.trim();
        document.body.appendChild(probe);
        const rgb = getComputedStyle(probe).color;
        probe.remove();
        return rgb;
      };
      return {
        // Окно вписано в свой контейнер: листать вбок не нужно.
        overflowRight: Math.round(frame.getBoundingClientRect().width - wrap.clientWidth),
        scrollable: wrap.classList.contains('is-scrollable'),
        // Кнопка в демо покрашена акцентом кабинета, а не розовым лендинга.
        ctaBackground: getComputedStyle(cta).backgroundColor,
        cabinetAccent: asRgb(styles.getPropertyValue('--cabp-accent')),
        landingAccent: asRgb(styles.getPropertyValue('--pink-ink')),
        cabinetAccentInk: asRgb(styles.getPropertyValue('--cabp-accent-ink')),
        // Названия этапов под окном не обрезаны полоской прогресса.
        stageLabel: document.querySelector('#loginScreen .aw-seg span')?.getBoundingClientRect()
          .height,
      };
    });
    expect(demo.scrollable).toBe(false);
    expect(demo.overflowRight).toBeLessThanOrEqual(1);
    expect(demo.ctaBackground).toBe(demo.cabinetAccent);
    // Розовые токены лендинга внутри демо переведены на акцент кабинета.
    expect(demo.landingAccent).toBe(demo.cabinetAccentInk);
    expect(demo.stageLabel).toBeGreaterThan(8);
  }
});

test('v39: выбранные снаружи страны видны на кнопке фильтра', async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem('mf_token', 'visual-fixture-token');
    sessionStorage.removeItem('talaria_in_app');
    // Сохранённый фильтр с двумя странами: ровно то, что кладёт кабинет сам
    // при уходе со страницы.
    localStorage.setItem(
      'talaria.search-filters.v1',
      JSON.stringify({ mode: 'manual', values: { leadMinSubs: '2000' }, countries: ['KZ', 'RU'] }),
    );
  });
  await page.goto('/index.html');
  await page.locator('[data-telegram-login-trigger]').first().click();
  await expect(page.locator('#appWrapper')).toHaveClass(/\bactive\b/);
  await page.locator('#loginScreen').evaluate((element) => {
    element.style.display = 'none';
  });
  await page.locator('#appWrapper').evaluate((element) => {
    element.style.display = 'block';
  });
  await activateCaptureSection(page, 'search');
  const picker = page.locator('.region-picker[data-for="leadCountry"] .region-picker-btn');
  await expect(picker).toContainText('Казахстан');
  await expect(picker).toContainText('Россия');
});

test('v40: меню аккаунта собирает тариф, лимиты, тему, помощь, поддержку и выход', async ({
  page,
}) => {
  await mockApi(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem('mf_token', 'visual-fixture-token');
    sessionStorage.removeItem('talaria_in_app');
  });
  await page.goto('/index.html');
  await page.locator('[data-telegram-login-trigger]').first().click();
  await expect(page.locator('#appWrapper')).toHaveClass(/\bactive\b/);
  await page.locator('#loginScreen').evaluate((element) => {
    element.style.display = 'none';
  });
  await page.locator('#appWrapper').evaluate((element) => {
    element.style.display = 'block';
  });
  await hideOnboardingOnly(page);

  // Боковое меню обязано вмещаться в экран: из-за квоты и плитки тарифа его
  // приходилось скроллить («сделай сайдбар без скролла»).
  const sidebar = await page.locator('#appWrapper .sidebar').evaluate((element) => ({
    scroll: element.scrollHeight - element.clientHeight,
    navScroll: (() => {
      const nav = element.querySelector('.nav');
      return nav.scrollHeight - nav.clientHeight;
    })(),
  }));
  expect(sidebar.scroll).toBeLessThanOrEqual(1);
  expect(sidebar.navScroll).toBeLessThanOrEqual(1);

  const menu = page.locator('#accountMenu');
  await expect(menu).toBeHidden();
  await page.locator('#sessionUserCard').click();
  await expect(menu).toBeVisible();
  // Тариф и остаток лимитов — то, за чем в меню и приходят.
  await expect(page.locator('#accMenuPlan')).toHaveText('Talaria');
  await expect(page.locator('#usageText')).toHaveText(/осталось/);
  await expect(page.locator('#accMenuLimits .acc-limit')).toHaveCount(2);
  // Курсор на нажимаемых элементах: отдельная просьба владельца.
  await expect(page.locator('#sessionUserCard')).toHaveCSS('cursor', 'pointer');
  await expect(page.locator('.acc-links button').first()).toHaveCSS('cursor', 'pointer');

  // Тема переключается прямо из меню и сразу применяется ко всей странице.
  await page.locator('#accMenuTheme [data-theme-choice="dark"]').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('#accMenuTheme [data-theme-choice="dark"]')).toHaveClass(/active/);
  await page.locator('#accMenuTheme [data-theme-choice="light"]').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  // Escape закрывает меню, а «Настройки» из него открывают раздел и отмечают его.
  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();
  await page.locator('#sessionUserCard').click();
  await page.locator('.acc-links [data-target="settings"]').click();
  await expect(page.locator('#settings')).toHaveClass(/\bactive\b/);
  await expect(page.locator('[data-target="settings"]')).toHaveClass(/\bactive\b/);
  await expect(menu).toBeHidden();
});

test('v40: центр помощи ищет по разделам и ведёт в поддержку', async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem('mf_token', 'visual-fixture-token');
    sessionStorage.removeItem('talaria_in_app');
  });
  await page.goto('/index.html');
  await page.locator('[data-telegram-login-trigger]').first().click();
  await expect(page.locator('#appWrapper')).toHaveClass(/\bactive\b/);
  await page.locator('#loginScreen').evaluate((element) => {
    element.style.display = 'none';
  });
  await page.locator('#appWrapper').evaluate((element) => {
    element.style.display = 'block';
  });
  await hideOnboardingOnly(page);

  await page.locator('#sessionUserCard').click();
  await page.locator('.acc-links [data-act="openHelpCenter"]').click();
  await expect(page.locator('#modalHelp')).toHaveClass(/show/);
  const items = page.locator('#helpList .help-item');
  const total = await items.count();
  expect(total).toBeGreaterThan(5);

  // Поиск идёт по тексту статьи, а не по заголовку: «промокод» есть только в
  // шагах про тариф, и статья открывается сразу.
  await page.locator('#helpSearch').fill('промокод');
  await expect(items).toHaveCount(1);
  await expect(items.first()).toHaveAttribute('data-help-id', 'tariff');
  await expect(items.first()).toHaveAttribute('open', '');
  // Слово из другой главы поднимает свою статью, а не ту же самую.
  await page.locator('#helpSearch').fill('Google Cloud Console');
  await expect(items.first()).toHaveAttribute('data-help-id', 'quota');

  await page.locator('#helpSearch').fill('такого слова нет нигде');
  await expect(page.locator('#helpList .dd-empty')).toBeVisible();

  // Из справки можно написать в поддержку: справка закрывается, диалог открыт.
  await page.locator('[data-act="helpToSupport"]').click();
  await expect(page.locator('#modalHelp')).not.toHaveClass(/show/);
  await expect(page.locator('#modalSupport')).toHaveClass(/show/);
});

test('v40: обращение в поддержку появляется в переписке', async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    localStorage.setItem('mf_token', 'visual-fixture-token');
    sessionStorage.removeItem('talaria_in_app');
  });
  await page.goto('/index.html');
  await page.locator('[data-telegram-login-trigger]').first().click();
  await expect(page.locator('#appWrapper')).toHaveClass(/\bactive\b/);
  await page.locator('#loginScreen').evaluate((element) => {
    element.style.display = 'none';
  });
  await page.locator('#appWrapper').evaluate((element) => {
    element.style.display = 'block';
  });
  await hideOnboardingOnly(page);

  // На телефоне бокового меню нет — аккаунт открывается из нижней навигации.
  await page.locator('.bottom-nav [data-act="toggleAccountMenu"]').click();
  const menu = page.locator('#accountMenu');
  // Пауза не для анимации: раньше тот же клик успевал закрыть панель через
  // обработчик «клик снаружи», и проверка ловила её в момент закрытия.
  await page.waitForTimeout(400);
  await expect(menu).toBeVisible();
  // Непрочитанный ответ поддержки виден до открытия диалога.
  await expect(page.locator('#accMenuSupportCount')).toHaveText('2');

  await page.locator('.acc-links [data-act="openSupport"]').click();
  await expect(page.locator('#modalSupport')).toHaveClass(/show/);
  await expect(page.locator('#supportThread .sup-empty')).toBeVisible();

  await page.locator('#supportInput').fill('Не приходит проверка Telegram');
  await page.locator('#supportSend').click();
  const bubble = page.locator('#supportThread .sup-msg');
  await expect(bubble).toHaveCount(1);
  await expect(bubble.first()).toHaveClass(/out/);
  await expect(bubble.first().locator('.sup-text')).toHaveText('Не приходит проверка Telegram');
  await expect(page.locator('#supportInput')).toHaveValue('');
});
test('v41: админка — тариф бинарный, деньги из активаций, журналы модалками', async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem('mf_token', 'visual-fixture-token');
    sessionStorage.removeItem('talaria_in_app');
  });
  await page.goto('/index.html');
  await page.locator('[data-telegram-login-trigger]').first().click();
  await expect(page.locator('#appWrapper')).toHaveClass(/\bactive\b/);
  await page.locator('#loginScreen').evaluate((element) => {
    element.style.display = 'none';
  });
  await page.locator('#appWrapper').evaluate((element) => {
    element.style.display = 'block';
  });
  await hideOnboardingOnly(page);

  // Включаем админский вид и открываем раздел, как это делает goTo.
  await page.evaluate(() => {
    window.__isAdmin = true;
    document.querySelectorAll('[data-admin-only]').forEach((el) => {
      el.style.display = '';
    });
    window.goTo('admin');
    window.loadAdmin();
  });
  await expect(page.locator('#admin')).toHaveClass(/\bactive\b/);

  // Сводка: пробные считаются отдельно от платных, «Риска оттока» больше нет.
  await expect(page.locator('#adminOverview')).toContainText('Платных подписок');
  await expect(page.locator('#adminOverview')).toContainText('пробных: 1');
  await expect(page.locator('#adminChurn')).toHaveCount(0);

  // Деньги приходят из активаций тарифа: формы «записать платёж» больше нет.
  await expect(page.locator('#payUserId')).toHaveCount(0);
  await expect(page.locator('#adminRevenue')).toContainText('оплат: 3');
  await expect(page.locator('#adminPayments .pay-row')).toHaveCount(1);

  // График активности: периоды переключаются, столбики отвечают на наведение.
  await expect(page.locator('#adminChart .adm-bar-col')).toHaveCount(14);
  await page.locator('[data-chart-period="today"]').click();
  await expect(page.locator('[data-chart-period="today"]')).toHaveClass(/is-active/);
  await expect(page.locator('[data-chart-period="today"]')).toHaveCSS('cursor', 'pointer');

  // «Показать все» открывает журналы модалками — с фильтрами внутри.
  await page.locator('[data-act="openLoginJournal"]').click();
  await expect(page.locator('#modalLoginJournal')).toHaveClass(/show/);
  await expect(page.locator('#loginReasonFilter')).toBeVisible();
  await page.locator('#modalLoginJournal .modal-close').click();
  await page.locator('[data-act="openActivityJournal"]').click();
  await expect(page.locator('#modalActivityJournal')).toHaveClass(/show/);
  await expect(page.locator('#activityScopeFilter')).toBeVisible();
});

test('v42: переписка — меню диалога, закрепление и «без звука»', async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem('mf_token', 'visual-fixture-token');
    sessionStorage.removeItem('talaria_in_app');
  });
  await page.goto('/index.html');
  await page.locator('[data-telegram-login-trigger]').first().click();
  await expect(page.locator('#appWrapper')).toHaveClass(/\bactive\b/);
  await page.locator('#loginScreen').evaluate((element) => {
    element.style.display = 'none';
  });
  await page.locator('#appWrapper').evaluate((element) => {
    element.style.display = 'block';
  });
  await hideOnboardingOnly(page);

  await page.evaluate(() => {
    window.goTo('messages');
  });
  await expect(page.locator('#messages')).toHaveClass(/\bactive\b/);
  await expect(page.locator('.chat-dialog')).toHaveCount(2);

  // Закреплённый диалог стоит первым — как в Telegram.
  await expect(page.locator('.chat-dialog').first()).toContainText('Пётр Партнёр');

  // Правый клик по строке открывает меню; «Закрепить» поднимает диалог наверх.
  await page.locator('.chat-dialog', { hasText: 'Иван Клиент' }).click({ button: 'right' });
  await expect(page.locator('#chatCtxMenu')).toBeVisible();
  await expect(page.locator('#chatCtxMenu')).toContainText('Закрепить');
  await expect(page.locator('#chatCtxMenu')).toContainText('Без звука');
  await expect(page.locator('#chatCtxMenu')).toContainText('Прочитано');
  await page.locator('#chatCtxMenu .chat-ctx-item', { hasText: 'Закрепить' }).click();
  await expect(page.locator('#chatCtxMenu')).toHaveCount(0);
  await expect(page.locator('.chat-dialog').first()).toContainText('Иван Клиент');
  await expect(page.locator('.chat-dialog').first().locator('.cd-pin')).toBeVisible();

  // Кнопка «⋯» в шапке открытого диалога даёт то же меню; mute виден в шапке.
  await page.locator('.chat-dialog', { hasText: 'Иван Клиент' }).click();
  await expect(page.locator('#chatRoom')).toBeVisible();
  await page.locator('#chatHeadMenu').click();
  await expect(page.locator('#chatCtxMenu')).toBeVisible();
  await page.locator('#chatCtxMenu .chat-ctx-item', { hasText: 'Без звука' }).click();
  await expect(page.locator('#chatHeadSub')).toContainText('без звука');
  await expect(
    page.locator('.chat-dialog', { hasText: 'Иван Клиент' }).locator('.cd-mute'),
  ).toBeVisible();

  // Esc закрывает меню, не трогая диалог.
  await page.locator('#chatHeadMenu').click();
  await page.keyboard.press('Escape');
  await expect(page.locator('#chatCtxMenu')).toHaveCount(0);
  await expect(page.locator('#chatRoom')).toBeVisible();
});
