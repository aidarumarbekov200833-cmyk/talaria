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
  const chatDialogs = [
    {
      peer_id: '777',
      kind: 'user',
      title: 'Иван Клиент',
      username: 'ivan',
      can_send: true,
      verified: false,
      is_self: false,
      unread: 2,
      muted: false,
      pinned: false,
      last_message: 'Привет!',
      last_out: false,
      last_date: Date.now() - 60000,
      has_photo: false,
    },
    {
      peer_id: '888',
      kind: 'user',
      title: 'Пётр Партнёр',
      username: 'petr',
      can_send: true,
      verified: false,
      is_self: false,
      unread: 0,
      muted: false,
      pinned: true,
      last_message: 'До связи',
      last_out: true,
      last_date: Date.now() - 7200000,
      has_photo: false,
    },
  ];
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
    if (url.pathname.startsWith('/api/chat/')) {
      // Переписка: два диалога, один закреплён. Состояние pin/mute живёт между
      // запросами теста — иначе не проверить, что список перерисовался честно.
      const chatBody =
        url.pathname === '/api/chat/accounts'
          ? [{ id: 1, label: 'Основной', username: 'sender', connected: true, is_primary: true }]
          : url.pathname === '/api/chat/dialogs'
            ? { account_id: 1, items: chatDialogs, next: null }
            : url.pathname === '/api/chat/history'
              ? {
                  items: [
                    { id: 1, date: Date.now() - 60000, text: 'Привет!', out: false },
                    { id: 2, date: Date.now() - 30000, text: 'Здравствуйте', out: true },
                  ],
                  next_offset_id: 0,
                }
              : url.pathname === '/api/chat/pin' || url.pathname === '/api/chat/mute'
                ? (() => {
                    const sent = JSON.parse(route.request().postData() || '{}');
                    const dialog = chatDialogs.find((item) => item.peer_id === sent.peer_id);
                    if (dialog && url.pathname === '/api/chat/pin') dialog.pinned = !!sent.pinned;
                    if (dialog && url.pathname === '/api/chat/mute') dialog.muted = !!sent.muted;
                    return { ok: true };
                  })()
                : { ok: true, image: '' };
      return route.fulfill({
        contentType: 'application/json',
        status: 200,
        body: JSON.stringify(chatBody),
      });
    }
    if (url.pathname.startsWith('/api/admin/')) {
      const adminBody =
        url.pathname === '/api/admin/overview'
          ? {
              online_now: 2,
              logins_today: 7,
              failed_logins_today: 1,
              events_today: 64,
              users_total: 12,
              users_new_7d: 3,
              active_subs: 2,
              trial_subs: 1,
              blocked_users: 0,
              leads_total: 5210,
              offers_total: 9,
              campaigns_total: 6,
              running_campaigns: 1,
              connected_accounts: 2,
              pending_verifications: 14,
              errors_today: 0,
              recent_visitors: [
                {
                  user_id: 2,
                  username: 'madi',
                  name: 'Мади',
                  ip: '10.0.0.2',
                  ok: true,
                  reason: '',
                  at: '2026-08-30T10:00:00Z',
                },
              ],
              recent_actions: [
                {
                  id: 1,
                  owner_id: 2,
                  username: 'madi',
                  user_name: 'Мади',
                  scope: 'search',
                  level: 'info',
                  message: 'Поиск каналов: криптообразование',
                  ref_type: '',
                  ref_id: null,
                  created_at: '2026-08-30T10:02:00Z',
                },
              ],
              by_day: [],
              revenue: {
                month: { KZT: 25000 },
                last_30_days: { KZT: 25000 },
                all_time: { KZT: 50000, RUB: 5000 },
                payments_total: 3,
                paying_users_this_month: 1,
              },
            }
          : url.pathname === '/api/admin/activity-series'
            ? {
                period: url.searchParams.get('period') || '14d',
                points: Array.from({ length: 14 }, (_, i) => ({
                  label: `${String(i + 1).padStart(2, '0')}.08`,
                  logins: (i % 5) + 1,
                  events: (i % 7) * 4 + 2,
                })),
              }
            : url.pathname === '/api/admin/payments'
              ? {
                  payments: [
                    {
                      id: 1,
                      owner_id: 2,
                      amount: 25000,
                      currency: 'KZT',
                      plan: 'pro',
                      months: 1,
                      promo_code: '',
                      note: 'первый месяц',
                      paid_at: '2026-08-20T09:00:00Z',
                      user_label: 'Мади',
                    },
                  ],
                  total: 1,
                  revenue: {
                    month: {},
                    last_30_days: {},
                    all_time: {},
                    payments_total: 1,
                    paying_users_this_month: 1,
                  },
                }
              : url.pathname === '/api/admin/users'
                ? { users: [], total: 0, scopes: {}, registration_open: true }
                : url.pathname === '/api/admin/support'
                  ? { threads: [], unread: 0 }
                  : url.pathname === '/api/admin/login-attempts'
                    ? { attempts: [], total: 0 }
                    : url.pathname === '/api/admin/activity'
                      ? { events: [], total: 0 }
                      : url.pathname === '/api/admin/promo-codes'
                        ? []
                        : {};
      return route.fulfill({
        contentType: 'application/json',
        status: 200,
        body: JSON.stringify(adminBody),
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
