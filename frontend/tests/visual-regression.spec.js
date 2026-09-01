import { expect, test } from '@playwright/test';
import { mockApi } from './helpers/mock-api.js';

// Эталонные скриншоты лендинга.
//
// Зачем: структурные тесты ловят пропавший элемент и переполнение по ширине, но
// не видят «поехало». Лендинг переделывался девять раз, и каждый раз вёрстку
// проверяли глазами по скриншотам — так регрессии и просачивались.
//
// Эталоны лежат в tests/visual-regression.spec.js-snapshots/. Обновлять только
// осознанно, когда дизайн действительно изменился:
//     npx playwright test visual-regression --update-snapshots
// и обязательно глазами просмотреть получившиеся PNG перед коммитом.
//
// Шрифты грузятся с Google Fonts. Если сборка идёт без сети, начертания
// подменятся системными и диффы будут ложными — тест это распознаёт и падает с
// понятным сообщением, а не с невнятной картинкой.

test.setTimeout(180_000);

const viewports = [
  ['desktop', 1440, 900],
  ['mobile', 390, 844],
];

const themes = ['dark', 'light'];

// Три сцены, которые ломались чаще всего: герой, гравюра тарифа и финал с
// вордмарком. Остальные секции — обычный текст, там регрессии видны структурным
// тестам.
const scenes = [
  ['hero', '#top'],
  ['pricing', '#pricing'],
  ['finale', '.zp-final-sticky'],
];

async function preparePage(page, theme) {
  await mockApi(page);
  await page.goto('/index.html', { waitUntil: 'domcontentloaded' });
  await page.evaluate((value) => {
    document.documentElement.setAttribute('data-theme', value);
  }, theme);

  // Без загруженных начертаний метрики текста другие, и диффы будут ложными.
  await page.waitForFunction(() => document.fonts.status === 'loaded', null, {
    timeout: 30_000,
  });
  const playfairLoaded = await page.evaluate(() =>
    document.fonts.check("700 40px 'Playfair Display'"),
  );
  expect(
    playfairLoaded,
    'Playfair Display не загрузился — эталоны сравнивать нельзя, проверьте доступ к fonts.gstatic.com',
  ).toBe(true);

  // Плавающие слои перекрыли бы сцены и меняли бы картинку от прогона к прогону.
  await page
    .locator('.drawer, .modal-overlay, .onb-overlay, .overlay-soft, .toast-wrap')
    .evaluateAll((elements) => {
      for (const element of elements) {
        element.classList.remove('show');
        element.style.display = 'none';
      }
    });
}

for (const [device, width, height] of viewports) {
  for (const theme of themes) {
    test(`эталон лендинга: ${device} / ${theme}`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await preparePage(page, theme);

      for (const [name, selector] of scenes) {
        // Липкая шапка (.lp-nav) едет вместе со скроллом и попадает в кадр
        // pricing/finale на случайной высоте — единственный источник флака в
        // этих эталонах. В hero шапка стоит на своём месте и является частью
        // сцены, поэтому там она остаётся видимой.
        await page.evaluate((sceneName) => {
          const nav = document.querySelector('.lp-nav');
          if (nav) nav.style.visibility = sceneName === 'hero' ? '' : 'hidden';
        }, name);

        const target = page.locator(selector).first();
        await target.scrollIntoViewIfNeeded();

        // Секции появляются по IntersectionObserver (класс .rv). Пока анимация
        // раскрытия не отработала, прозрачность ещё меняется.
        await page.waitForTimeout(1200);

        // Ладонь и письмо в финале грузятся лениво. Без этого ожидания в эталон
        // однажды попал серый прямоугольник вместо письма — и такой эталон
        // «зафиксировал» бы баг как норму.
        await page.waitForFunction(
          () =>
            [...document.images].every(
              (image) => !image.currentSrc || (image.complete && image.naturalWidth > 0),
            ),
          null,
          { timeout: 30_000 },
        );
        await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => r())));

        await expect(target).toHaveScreenshot(`${name}-${device}-${theme}.png`, {
          animations: 'disabled',
          caret: 'hide',
          // Порог подобран экспериментально, а не на глаз. Сначала стоял
          // maxDiffPixelRatio: 0.02 — и тест не заметил намеренно сдвинутые на
          // 60px кнопки: доля в 2% от кадра 1440x900 прощает 26 000 пикселей.
          // threshold гасит шум сглаживания попиксельно, а maxDiffPixels держит
          // жёсткий абсолютный потолок, который не растёт вместе с кадром.
          threshold: 0.25,
          maxDiffPixels: 600,
          timeout: 30_000,
        });
      }
    });
  }
}
