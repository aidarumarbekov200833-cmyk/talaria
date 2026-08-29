# Zephyr Finpay-Style Redesign and Safe Refactor Design

## Goal

Adapt the visual grammar of the Finpay reference to the Russian-language Zephyr
landing page and authenticated workspace while preserving every existing public
DOM, HTTP, storage, payment, and Telegram contract. Refactor by compatibility
facades and vertical slices so every checkpoint remains runnable.

## Product and visual system

- Zephyr finds YouTube channels, stores lead databases, prepares contextual
  offers, and sends them through connected Telegram accounts.
- The interface uses Montserrat only. Light theme: fog `#EEF1F4`, white
  surfaces, ink `#0E2A38`, one electric-blue accent `#315DFF`. Dark theme uses
  ink surfaces and the same accent. Success, warning, and danger colors are
  reserved for feedback states.
- Layout follows a 1440px, 12-column Finpay-like rhythm with 24/18/12px radii,
  large section surfaces, generous whitespace, and high-density presentation
  only inside tables, lists, and logs.
- The landing sequence is: navigation; 55/45 hero with three overlapping
  Zephyr product cards; public metrics; three benefits; bento facts; an ink
  1/2/3 workflow; account safety; one Zephyr Access tariff; FAQ; ink CTA;
  column footer.
- The workspace keeps the existing sidebar, topbar, mobile drawer, and eight
  sections. It applies the same visual system and allows at most one bright
  primary action per section.
- No emoji, stock photography, faces, decorative blob gradients, invented
  limits, placeholder copy, or actionless controls. The Z/airflow logo remains.

## Motion and accessibility

- Motion tokens are 250/360/480ms with
  `cubic-bezier(0.22, 1, 0.36, 1)`. Section reveal is 420ms with a 70ms
  stagger; hover lift is 3px; dialogs use 320ms; price uses a 400ms rAF tween;
  metrics use a 1000ms rAF tween.
- Use transform and opacity except for the necessary FAQ panel height.
- `prefers-reduced-motion: reduce` exposes final content immediately and
  disables parallax, reveal, and numeric interpolation.
- Native dialogs retain modal focus containment, Escape close, and focus
  return. FAQ controls expose `aria-expanded`. All controls have visible focus.

## Immutable contracts

- Preserve every original `id`, class token, `name`, `data-act`, `data-args`,
  `data-target`, and `data-telegram-login-trigger`. Reordering, wrapping, and
  adding classes are allowed.
- Preserve section ids `dashboard`, `search`, `databases`, `offer`, `mailing`,
  `tariffs`, `settings`, and `admin`; preserve every explicitly named landing,
  FAQ, pricing, and Telegram-login node from the implementation plan.
- Preserve localStorage keys `mf_token`, `mf_theme`, and
  `zephyr_active_section`.
- Preserve every OpenAPI path, method, status, success schema, and the existing
  `detail/request_id/errors` error envelope.
- Preserve manual Telegram payment and all reply-watcher semantics, including
  exact ID precedence, username fallback only when the stored ID is absent,
  atomic one-shot matching, and the 300-second test watcher.

## Pricing and feedback

- The only public product is Zephyr Access: 5,000 RUB or 25,000 KZT monthly,
  three months paid once at 15% off, and a valid promo at 30% off the first
  month. Server quotes are the source of truth.
- The UI never reveals the promo code. A quiet link opens a side-fading input
  with Apply and Close. Success is inline green; failure is calm inline error.
- Payment remains confirmation followed by a prepared administrator Telegram
  message containing product, period, currency, promo state, and total.
- Every async surface has loading, content, empty, error, and retry behavior.
  Public-stat failure keeps em dashes and says `Данные временно недоступны`.

## Architecture

- Frontend remains vanilla ES modules. CSS is split behind the compatible
  `styles.css` entrypoint. New shared modules cover motion, storage, DOM-safe
  rendering, CSV, dialogs, landing, FAQ, and tariffs. Existing entrypoints
  remain facades until all consumers migrate.
- FastAPI routers become HTTP adapters, domain services own transactions, and
  repositories own SQLAlchemy access without commits. Existing imports remain
  available through package facades.
- Delivery orchestration remains in Python for durable state and recovery.
  GramJS remains a transport and is split internally behind `server.js`.
- No runtime dependency is added. Playwright and Prettier are development-only;
  Node gateway tests use `node:test`.

## Verification

- External ZIP and SHA-256 manifests replace Git checkpoints by explicit user
  choice.
- DOM and OpenAPI baselines are machine-readable and blocking.
- Every feature/refactor follows red-green-refactor and passes its focused gate
  before the full suite.
- Final acceptance covers desktop/mobile, both themes, reduced motion, console,
  overflow, dialogs, pricing, API parity, migrations, query counts, Telegram
  reply guards, Docker builds, and live health smoke.
