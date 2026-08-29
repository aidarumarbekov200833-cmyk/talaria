// Детерминированные ответы API для визуальных тестов. Вынесено из
// visual-structure.spec.js, чтобы эталоны скриншотов и структурные
// проверки видели ровно одни и те же данные.
export async function mockApi(page) {
  const plan = {
    slug: 'zephyr',
    name: 'Talaria',
    tagline: 'Все инструменты для персональных Telegram-кампаний',
    price_rub: 5000,
    price_kzt: 25000,
    price_rub_first: 5000,
    price_kzt_first: 25000,
    monthly_leads: -1,
    monthly_messages: -1,
    monthly_ai_offers: -1,
    max_per_launch: -1,
    max_databases: -1,
    max_offers: -1,
    max_warmup: -1,
    ai: true,
    ai_offer: true,
    features: [
      'Поиск и проверка лидов по YouTube',
      'Базы лидов, офферы и Telegram-рассылки',
      'Центр безопасности аккаунтов и автопауза',
    ],
  };
  const quote = {
    plan_slug: 'zephyr',
    plan_name: 'Talaria',
    period_months: 1,
    currency: 'RUB',
    base_total: 5000,
    total_amount: 5000,
    bundle_discount: 0,
    promo_discount: 0,
    base_total_rub: 5000,
    bundle_discount_rub: 0,
    promo_discount_rub: 0,
    total_rub: 5000,
    promo_code: '',
    promo_valid: false,
    promo_message: '',
  };
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    const body =
      url.pathname === '/api/subscription'
        ? {
            plan: 'zephyr',
            plan_name: 'Talaria',
            stored_plan: 'zephyr',
            status: 'active',
            blocked: false,
            paid_until: null,
            expired: false,
            ai: true,
            ai_offer: true,
            note: '',
            admin_contact: 'zephyr_admin',
            usage: {},
            plans: [plan],
            first_purchase: true,
            first_purchase_discount_pct: 0,
          }
        : url.pathname === '/api/plans'
          ? {
              plans: [plan],
              currencies: ['RUB', 'KZT'],
              admin_contact: 'zephyr_admin',
              first_purchase_discount_pct: 0,
            }
          : url.pathname === '/api/plans/quote'
            ? quote
            : {};
    return route.fulfill({
      contentType: 'application/json',
      status: 200,
      body: JSON.stringify(body),
    });
  });
}
