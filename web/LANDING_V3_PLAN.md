# Landing Page v3 — Plan

## 1. Current-State Assessment (v2)

**Sections (top → bottom):** Sticky nav → Hero (badge + headline + CTAs + banner image w/ parallax) → Stats strip (5 items) → Features bento (6 cards, 1 featured) → How It Works (3 steps) → Architecture / Namespaces (8 cards + connect bar) → Final CTA → Footer.

**Strengths:**
- Clean scroll-reveal + parallax hooks with `prefers-reduced-motion` respect.
- Consistent design language: ambient orbs, glass-card / shell-chip system, `--pc-*` CSS custom properties throughout.
- Accessibility: skip-link, `aria-label` on every section, keyboard-focusable.
- Good responsive breakpoints (sm/md/lg) for bento grid, how-steps, stats.

**Weaknesses / Gaps:**
- **No social proof or community signals** — no GitHub stars, user count, or testimonials.
- **No live demo / interactive element** — everything is static text + static screenshot.
- **Nav is thin** — only "Features" scroll-link + "Launch Dashboard" CTA. No Docs / GitHub / Architecture anchors.
- **Stats strip is vague** — "Rust", "8", "Neo4j" are labels, not metrics. No dynamic data.
- **"How It Works" is abstract** — no code snippets, CLI examples, or visual diagram.
- **Architecture section is flat** — namespace cards are list-like; no graph visualization.
- **Footer is minimal** — no links to docs, GitHub, changelog.
- **No open-source callout** — MIT/Apache dual license isn't surfaced on the landing page.
- **Banner image is a static PNG** — no fallback skeleton / loading state beyond `display:none`.
- **Single CTA verb everywhere** — "Launch Dashboard" ×3 is repetitive.

---

## 2. v3 Concept

**Theme: "Show, don't tell."**

Evolve from a marketing-brochure feel to a product-forward landing page that lets visitors *experience* Revka in seconds. Add interactive/dynamic elements, social proof, and clearer information hierarchy while keeping the existing design system intact.

**Design principles:**
- Reuse every existing CSS primitive (glass-card, shell-chip, btn-electric, orbs, reveal hooks).
- No new npm dependencies. Leverage only React 19 + Lucide + existing Tailwind 4.
- Keep total new CSS under ~120 lines; extend, don't replace.
- Every new section must honor `prefers-reduced-motion` via the existing `useReveal` / `useParallax` hooks.

---

## 3. Sections — Add / Refine

### 3a. Nav — Refine

| Change | Detail |
|--------|--------|
| Add anchor links | "Features", "How It Works", "Architecture" — smooth-scroll to `id` anchors |
| Add external links | "Docs" (→ `/docs/`), "GitHub" (external icon) |
| Mobile hamburger | `sm:hidden` menu button → slide-down panel with same links |

### 3b. Hero — Refine

| Change | Detail |
|--------|--------|
| Tagline variation | Change sub-headline to `"The agent runtime that remembers everything"` — benefit-led, not jargon-led |
| CLI preview snippet | Below CTAs, add a `<code>` block: `revka agent -m "Plan the migration"` with a blinking cursor + copy button. Uses `shell-chip` styling. |
| CTA differentiation | Primary: "Launch Dashboard" → **"Open Dashboard"**. Secondary: "Explore Features" → **"View on GitHub"** (external link). Avoids 3× identical CTAs. |
| Banner skeleton | While image loads, show a shimmer placeholder (CSS-only, 16:9 aspect-ratio box). |

### 3c. Stats Strip — Refine → "Numbers at a Glance"

Replace vague labels with quantifiable metrics (can be hardcoded initially, wired to API later):

| Stat | Example |
|------|---------|
| Agents spawned | `1,240+` |
| Memory nodes | `86k` |
| Namespaces | `8` |
| Avg recall latency | `< 12 ms` |
| Uptime | `99.9%` |

### 3d. NEW: "See It In Action" — Interactive Terminal Section

**Position:** Between Stats strip and Features bento.

- Fake terminal component (`<div>` styled as a dark terminal window with title bar dots).
- 3–4 pre-recorded command/response pairs that auto-type on scroll-reveal:
  1. `revka agent -m "What did I work on yesterday?"` → memory recall response
  2. `revka teams spawn --preset review-squad` → team spawn output
  3. `revka hub search "code-review"` → ClawHub search results
- User can click tabs to switch between examples (no auto-advance).
- All text is hardcoded strings — no API calls.
- Typing animation: CSS `@keyframes` + `steps()` for the command, instant reveal for the response.
- Honor `prefers-reduced-motion`: skip typing, show final state immediately.

### 3e. Features Bento — Refine

| Change | Detail |
|--------|--------|
| Add hover micro-interaction | On hover, icon ring scales 1.08 + subtle glow pulse (CSS only). |
| Add "Learn more →" link on featured card | Points to `/docs/` anchor for Kumiho memory. |

### 3f. "How It Works" — Refine

| Change | Detail |
|--------|--------|
| Replace abstract text with concrete examples | Step 1: show `revka agent create` CLI snippet. Step 2: show a mini graph diagram (SVG inline, 3 nodes + 2 edges). Step 3: show a query example `revka memory query "trust > 0.8"`. |
| Connector line upgrade | Animate a dot traveling along the connector (CSS `@keyframes`, 3s loop). |

### 3g. Architecture — Refine

| Change | Detail |
|--------|--------|
| Visual hierarchy | Group namespaces into 2 categories: "Agent State" (AgentPool, Plans, Sessions, Goals, AgentTrust) and "Ecosystem" (ClawHub, Teams, Skills). Use subheadings. |
| Mini graph visualization | Replace connect-bar with a simple SVG showing nodes + edges between the 8 namespaces. Static SVG, no library needed. Orb glow behind it. |

### 3h. NEW: "Open Source" Callout Section

**Position:** Between Architecture and Final CTA.

- Centered badge: `MIT OR Apache-2.0`
- One-liner: "Revka is open source. Fork it, extend it, own it."
- Two buttons: "View on GitHub" (external), "Read the Docs" (internal).
- Minimal — 6 lines of JSX, reuse `shell-chip` + `btn-electric` + `glass-card`.

### 3i. Final CTA — Refine

| Change | Detail |
|--------|--------|
| Differentiate from hero CTA | Headline: "Your agents deserve a memory." CTA: "Get Started" (→ `/dashboard`). |
| Add secondary CTA | "Join the community" → GitHub Discussions link. |

### 3j. Footer — Refine

| Change | Detail |
|--------|--------|
| Add link columns | Col 1: Product (Dashboard, Agents, Teams). Col 2: Resources (Docs, GitHub, Changelog). Col 3: Legal (License). |
| Keep tech-chip row | Already good — no change. |

---

## 4. File Touch List

| File | Action | Scope |
|------|--------|-------|
| `src/pages/Landing.tsx` | Edit | All changes above: nav links, hero CLI snippet, terminal section, open-source section, footer links, refined copy, stats data, how-it-works examples |
| `src/index.css` | Edit | ~80–120 new lines: `.terminal-window`, `.terminal-titlebar`, `.terminal-typing`, `.typing-cursor`, `.feature-card:hover .feature-icon` glow, `.oss-callout`, `.footer-links`, `.nav-mobile-menu`, banner skeleton shimmer, animated connector dot |
| `src/App.tsx` | No change | Landing route is already `/`, no new routes needed |
| `web/package.json` | No change | No new dependencies |
| `web/public/*` | No change | Existing `revka-banner.png` and `revka-trans.png` are sufficient |

**Total files changed: 2** (`Landing.tsx`, `index.css`).

---

## 5. Validation Checklist

- [ ] `npm run typecheck` passes (no TS errors in Landing.tsx)
- [ ] `npm run build` succeeds (Vite production build, no warnings)
- [ ] All 6 sections render on `/` route with no console errors
- [ ] Scroll-reveal fires correctly for every new section (IntersectionObserver)
- [ ] `prefers-reduced-motion: reduce` — no animations, all content immediately visible
- [ ] Mobile (375px): hamburger menu works, terminal section stacks, bento is 1-col, footer links wrap
- [ ] Tablet (768px): bento 2-col, how-steps 3-col, terminal tabs are tappable
- [ ] Desktop (1280px+): full layout, parallax active, terminal typing animation runs
- [ ] Keyboard navigation: all interactive elements (nav links, CTAs, terminal tabs, footer links) are focusable and operable
- [ ] Skip-link still jumps to `#main-content`
- [ ] No new `npm` dependencies introduced
- [ ] Banner image `onError` fallback still hides gracefully
- [ ] External links (GitHub) open in new tab with `rel="noopener noreferrer"`
- [ ] CTA verbs are distinct across sections (no repeated "Launch Dashboard" ×3)
- [ ] Lighthouse accessibility score stays ≥ 90
