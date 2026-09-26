
## Product brief

Contact Outreach is a private Spanish-language operations console for a small B2B sales team in Argentina. It manages contacts, email campaigns sent through Gmail, LLM-drafted replies, human review queues, PDF catalogs, and the safety controls that govern automated sending.

**The users are salespeople, not engineers.** They do not know what a webhook, an embedding, a confidence threshold, or a dry run is. They need to know three things on every screen: what changed, what needs their attention, and what is safe to do next.

The product's real subject matter is **industrial supply and repair** — motor rewinding, electromechanical workshops, pumps, industrial maintenance. It is a workshop instrument, not a marketing SaaS. Design it like a control panel someone checks twice a day, not like a landing page.

## Aesthetic direction (committed — do not reinterpret)

Precise, dense, instrument-like. Every value below is fixed. If a value you need is not listed, derive it from the scale; do not invent a new one.

### Color

```
--canvas          #F4F4F1   page background (neutral stone, not beige, not blue-gray)
--surface         #FFFFFF   cards, tables, panels
--surface-sunken  #EDEDE9   table headers, inset areas, code blocks
--border          #DEDEDA   default 1px border
--border-strong   #C4C4BE   dividers that separate major regions
--ink             #16181C   primary text
--ink-secondary   #4A4F57   labels, secondary text (min 7:1 on surface)
--ink-muted       #6E747E   metadata, timestamps, placeholder (min 4.5:1)

--accent          #1E4B8F   primary actions, active nav, links
--accent-hover    #173C73
--accent-quiet    #E8EEF7   selected rows, active nav background
--focus           #3B7DD8   focus ring only — never a fill

--success         #1F6E43   --success-bg #E7F1EA
--warning         #8A5A00   --warning-bg #F6EEDC
--danger          #A32B24   --danger-bg  #F7E8E6
--inactive        #6E747E   --inactive-bg #ECECE8
```

Rules:
- These are the only colors in the application. No tints, no opacity variants, no gradients anywhere.
- Semantic colors are **never** the only signal. Every status needs an icon or a word alongside the color.
- `--accent` is for interactive and navigational elements only. It is never decorative and never a background fill for a large area.
- Do not use green for "blocked," "paused," or "stopped," even when stopped is the safe state. Use `--inactive` for stopped, `--success` only for "working correctly."

### Typography

Two families, loaded via `next/font/google`:

```
Body / UI:  IBM Plex Sans     — 400, 500, 600
Data / IDs: IBM Plex Mono     — 400, 500
```

`font-variant-numeric: tabular-nums` on every number, every table cell, every metric, every timestamp. Non-negotiable.

Use IBM Plex Mono for: email addresses, campaign IDs, timestamps, counts in tables, technical values inside progressive-disclosure sections. Nothing else.

Scale — six sizes, no others:

```
display   24px / 30px / 600   page titles only
title     18px / 24px / 600   section and card headers
body      14px / 20px / 400   default
body-md   14px / 20px / 500   emphasis, table headers, labels
small     13px / 18px / 400   help text, metadata
micro     11px / 14px / 500   badges, eyebrows, uppercase labels, +0.04em tracking
```

Notes:
- The current design uses a ~34px near-black title over faint gray. That weight contrast is the tell. 24px/600 in `--ink` over 14px in `--ink-secondary` is the replacement.
- Never go above 600 weight. Never below 400.
- Sentence case everywhere except `micro` eyebrows.

### Space

4px base. Permitted values only: `4, 8, 12, 16, 24, 32, 48`.

```
Card padding             16px (compact) / 20px (default)
Gap between sections     24px
Gap between form fields  16px
Gap between form groups  32px
Page gutter              24px desktop / 16px tablet / 12px mobile
```

### Radius

```
--r-sm   3px   badges, tags, inputs, buttons
--r-md   6px   cards, panels, modals, popovers
```

Two values. Nothing rounder. Small radius reads as instrumentation; 12–16px radius reads as consumer app.

### Borders and elevation

**Cards get a 1px `--border` and no shadow.** A 1px gray border *plus* a soft drop shadow is one of the most reliable machine-made tells. Pick one; we picked the border.

Shadows exist only for things that genuinely float above the page:

```
--elev-1   0 2px 8px rgba(22,24,28,0.08)    dropdowns, popovers, tooltips
--elev-2   0 8px 32px rgba(22,24,28,0.14)   modals, drawers
```

Sticky headers and sticky save bars use a 1px `--border-strong` bottom/top edge, not a shadow.

### Density

```
Sidebar expanded          240px
Sidebar collapsed         64px
Topbar height             52px
Table row height          40px
Table header height       36px
Content max-width         1360px
Form column max-width     640px   (a phone field must never be 1500px wide)
Reading text max-width    68ch
Minimum tap target        36px desktop, 44px touch
```

### Motion

```
--dur-fast   120ms   hover, focus, color change
--dur-base   180ms   expand/collapse, filter reflow, tab change
--dur-slow   240ms   drawer, modal, route transition
--ease       cubic-bezier(0.2, 0, 0, 1)
```

Rules:
- Animate `opacity` and `transform` only. Never `height`, `width`, `top`, or `left`.
- No spring physics on UI chrome. No stagger longer than 40ms per item, max 6 items.
- Motion communicates cause and effect: a saved form flashes its section border to `--success` for 900ms then returns. A filtered table cross-fades rows over `--dur-base`. A status change animates the badge, not the whole row.
- No looping animation of any kind. No pulsing, no skeleton shimmer that runs forever, no animated backgrounds, no parallax.
- Wrap everything in `@media (prefers-reduced-motion: reduce) { * { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; } }` and verify it.

## Ant Design configuration

Keep Ant Design. Do not add a second component library, do not add Tailwind, do not hand-roll replacements for `Table`, `Form`, `Select`, `Modal`, or `DatePicker`.

Theme it through `ConfigProvider` design tokens — Ant Design v5 uses CSS-in-JS with a token model, so LESS overrides and CSS `!important` patches are both wrong. Global tokens go in `theme.token`; per-component overrides go in `theme.components`.

Create `src/theme/tokens.ts` exporting the raw values above, and `src/theme/antdTheme.ts`:

```ts
import type { ThemeConfig } from 'antd';

export const antdTheme: ThemeConfig = {
  cssVar: true,
  hashed: false,
  token: {
    colorPrimary: '#1E4B8F',
    colorSuccess: '#1F6E43',
    colorWarning: '#8A5A00',
    colorError:   '#A32B24',
    colorInfo:    '#1E4B8F',
    colorTextBase: '#16181C',
    colorBgBase:   '#FFFFFF',
    colorBgLayout: '#F4F4F1',
    colorBorder:          '#DEDEDA',
    colorBorderSecondary: '#EDEDE9',
    borderRadius:    6,
    borderRadiusSM:  3,
    borderRadiusLG:  6,
    fontFamily: 'var(--font-plex-sans), system-ui, sans-serif',
    fontSize: 14,
    lineHeight: 1.43,
    controlHeight: 34,
    boxShadow:          '0 2px 8px rgba(22,24,28,0.08)',
    boxShadowSecondary: '0 8px 32px rgba(22,24,28,0.14)',
    motionDurationFast: '0.12s',
    motionDurationMid:  '0.18s',
    motionDurationSlow: '0.24s',
    wireframe: false,
  },
  components: {
    Layout: { siderBg: '#FFFFFF', headerBg: '#FFFFFF', bodyBg: '#F4F4F1', headerHeight: 52 },
    Menu:   { itemBg: 'transparent', itemSelectedBg: '#E8EEF7', itemSelectedColor: '#1E4B8F',
              itemHeight: 34, itemMarginInline: 8, itemBorderRadius: 3 },
    Table:  { headerBg: '#EDEDE9', headerColor: '#4A4F57', cellPaddingBlock: 10,
              rowHoverBg: '#F7F7F4', borderColor: '#EDEDE9' },
    Card:   { paddingLG: 20, boxShadowTertiary: 'none' },
    Button: { primaryShadow: 'none', defaultShadow: 'none', dangerShadow: 'none', fontWeight: 500 },
    Input:  { paddingBlock: 6 },
    Tag:    { borderRadiusSM: 3 },
  },
};
```

Mount with `AntdRegistry` from `@ant-design/nextjs-registry` in the root layout, wrapping `ConfigProvider`, and wrap the tree in antd's `<App>` so `message` and `Modal` pick up the theme (static `message.xxx` / `Modal.xxx` calls do **not** inherit `ConfigProvider` context — use `App.useApp()` or `Modal.useModal()`).

Import `antd/dist/reset.css` once. Never write a `.ant-*` CSS selector. If a component can't be themed with tokens, wrap it in your own component instead.

## Copy rules

Spanish, Rioplatense register, sentence case, active voice. Preserve existing copy where it already works — most of it does.

**The most important rule in this file:** name things by what the user controls, never by how the system is built. Internal state names, enum values, provider names, and model identifiers must never appear as the primary label in the UI.

Proposed label mapping. **The agent must not change any stored value, API payload, or enum — this is display text only.** Where a mapping is ambiguous, add it to `QUESTIONS.md` rather than guessing:

| Internal value | Displayed label | Displayed explanation |
|---|---|---|
| `dry-run` | Simulación | Se prepara todo pero no se envía ningún email. |
| `LIVE` (envío) | Envío real | Los emails salen de verdad a los contactos. |
| `SHADOW` | Observación | El sistema redacta respuestas pero no las envía. |
| `OFF` | Desactivada | El sistema no redacta ni envía respuestas. |
| `DISCONNECTED` | Sin conectar | (with a "Conectar" action beside it) |
| `CONNECTED` | Conectada | (show which account) |
| `bloqueado` / `bloqueadas` | Detenido | Use `--inactive`, not green. |
| `fake`, `fake-deterministic` | Sin proveedor real configurado | Show raw value only under "Detalles técnicos". |
| `Confianza mínima: 0.750` | Confianza mínima: 75% | |
| `text-embedding-3-small` | — | Hide entirely behind "Detalles técnicos". |

Anything the user cannot act on and cannot understand goes inside a collapsed `Detalles técnicos` section at the bottom of the page, never in the main flow.

Failure and empty states give direction, not mood. Errors say what happened and what to do; they do not apologize and are never vague. An empty screen is an invitation to act: every empty state needs a one-line explanation and a primary action (or an explanation of why no action is available to this role).

## Safety rules — these override every design rule above

The backend already enforces these. The UI must never make them *feel* softer than they are.

1. Do not change any API contract, request shape, permission check, campaign state transition, Gmail safety check, suppression rule, or kill-switch behavior. Frontend only.
2. Preserve dry-run as the default. It must be visible at a glance from anywhere in the app — put the current sending mode in the topbar, permanently.
3. **Never hide why an action is unavailable.** A disabled button always carries a tooltip and, where there's room, adjacent text naming the exact blocker ("Falta aprobar el contenido", "Gmail no está conectado"). Never render a bare grayed-out control.
4. Never make live sending or automatic replies easier to trigger. Specifically:
   - The "Activar LIVE" password field must be `autoComplete="off"` / `autoComplete="new-password"`. Right now the browser autofills it, which puts real sending one click away.
   - Arming live sending requires a modal that states the consequence in plain Spanish, lists what will happen, and requires typing a confirmation word — not just a click.
   - The confirm button in any destructive or irreversible modal is `--danger` and is the *right-hand* button; the safe default has focus on open.
5. Do not invent features, states, metrics, contacts, or workflow states. If the API doesn't return it, it doesn't render. If a metric has no data, show a real empty treatment — never a plausible-looking number, never a demo chart.
6. Role awareness: VENDEDOR is read-only across summaries, campaigns, sent emails, contacts and conversations. Admin-only controls are **hidden**, not disabled, for VENDEDOR — a disabled control they can never enable is noise. Sections that are entirely admin-only don't render for them at all.

## Banned list

The agent must not produce any of these:

- Purple/indigo/violet anything. Gradients of any kind, including on buttons and icons.
- Glassmorphism, backdrop blur, frosted panels, glow effects.
- Emoji as UI iconography.
- Inter, Roboto, or the system default stack as the display face.
- A row of three or four equal-weight cards with an icon on top. (This is exactly what Resumen does today.)
- Card with border *and* shadow.
- Decorative illustrations. The Ant Design default `Empty` fax-machine graphic is banned outright — replace with a purpose-built empty state per page.
- Full-viewport-width form inputs.
- Dark mode. Not requested, not needed, adds a whole second surface to maintain.
- A badge/pill floating above a heading.
- Numbered 01 / 02 / 03 markers unless the content genuinely is an ordered sequence.
- Any `!important`, any `.ant-*` selector override.
- Icon-only buttons without `aria-label` and a tooltip.
- Any metric, chart, or count not backed by a real API response.

## Icon rules

`@ant-design/icons` only — it's already a dependency and stylistically consistent. 16px in navigation and buttons, 14px inline in text. One icon per nav item, one per status badge, one per action button where the action is ambiguous. No icons in headings, no icons in body text, no icons purely for visual rhythm.

## Self-review checklist

Run this against every page before reporting it done. Report the result explicitly.

- [ ] Every color used appears in the token list above. No exceptions.
- [ ] Every font size is one of the six in the scale.
- [ ] Every spacing value is in `4, 8, 12, 16, 24, 32, 48`.
- [ ] All numbers use tabular figures.
- [ ] The page has loading, empty, error, and role-restricted states — all four, all built, not stubbed.
- [ ] No raw enum, provider name, or model identifier is visible outside "Detalles técnicos".
- [ ] Every disabled control explains why it's disabled.
- [ ] Every icon-only control has a tooltip and an `aria-label`.
- [ ] Keyboard: tab order is sensible, focus ring is visible on `--canvas` and `--surface`, focus is trapped in modals and returns on close.
- [ ] `prefers-reduced-motion` verified in the browser, not assumed.
- [ ] Works at 1440px, 1024px, 768px, and 390px. Tables become stacked rows or a horizontally scrolling region with a pinned first column — never a squashed table.
- [ ] Contrast: `--ink-muted` on `--surface` ≥ 4.5:1, all interactive text ≥ 4.5:1.
- [ ] No API call, payload, permission check, or state transition was modified.
- [ ] Nothing on screen is invented.
