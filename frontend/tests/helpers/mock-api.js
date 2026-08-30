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
  // Переписка с поддержкой живёт между запросами одного теста: отправили —
  // значит, следующая загрузка диалога обязана показать это сообщение.
  const supportThread = [];
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/support/messages') {
      if (route.request().method() === 'POST') {
        const sent = JSON.parse(route.request().postData() || '{}');
        supportThread.push({
          id: supportThread.length + 1,
          author: 'user',
          text: sent.text || '',
          created_at: new Date().toISOString(),
          read: false,
        });
        return route.fulfill({
          contentType: 'application/json',
          status: 200,
          body: JSON.stringify(supportThread.at(-1)),
        });
      }
      return route.fulfill({
        contentType: 'application/json',
        status: 200,
        body: JSON.stringify({ messages: supportThread, unread: 0, can_write: true }),
      });
    }
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
            // Единый тариф Talaria без продуктовых лимитов: -1 = «без лимита».
            usage: {
              leads_used: 1214,
              leads_limit: -1,
              messages_used: 312,
              messages_limit: -1,
              databases: 4,
              databases_limit: -1,
              offers: 12,
              offers_limit: -1,
            },
            plans: [plan],
            first_purchase: true,
            first_purchase_discount_pct: 0,
          }
        : url.pathname === '/api/quota'
          ? {
              used_today: 3800,
              total_daily: 10000,
              left_today: 6200,
              keys_enabled: 1,
              keys_total: 1,
              projects: 1,
              source_note: '',
            }
          : url.pathname === '/api/support/unread'
            ? { unread: 2 }
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
