# TradeCore — Design System
*Version 1.0 — April 2026*

---

## Design Philosophy

TradeCore is built for traders who make decisions under pressure. Every design choice serves one goal: **clarity under stress**. Data must be instantly readable, alerts must be impossible to miss, and the interface must never slow down a decision.

Three guiding principles:
- **Density without clutter** — show a lot, but make it feel calm
- **Signal over noise** — critical information always wins visual hierarchy
- **Speed feels safe** — fast transitions and live data build confidence

---

## 1. Color Palette

### Background Scale
Used for layering depth — darker = further back, lighter = closer/interactive.

| Token | Hex | Usage |
|-------|-----|-------|
| `bg-canvas` | `#070C18` | Page background, deepest layer |
| `bg-primary` | `#0B1120` | Main content areas |
| `bg-secondary` | `#111827` | Sidebar, secondary panels |
| `bg-card` | `#1A2235` | Cards, module panels |
| `bg-elevated` | `#1F2D42` | Dropdowns, modals, tooltips |
| `bg-hover` | `#243350` | Hover states on interactive elements |

### Brand & Primary
| Token | Hex | Usage |
|-------|-----|-------|
| `primary-400` | `#60A5FA` | Hover state |
| `primary-500` | `#3B82F6` | Primary buttons, links, active states |
| `primary-600` | `#2563EB` | Pressed state |
| `primary-subtle` | `#1E3A5F` | Backgrounds behind primary elements |
| `primary-glow` | `rgba(59,130,246,0.15)` | Alert halos, card borders on active |

### Semantic Colors
These carry meaning — never use for decoration.

| Token | Hex | Meaning |
|-------|-----|---------|
| `profit` / `success` | `#10B981` | Price up, winning trade, bullish signal |
| `profit-subtle` | `#064E3B` | Background for profit cells |
| `loss` / `danger` | `#EF4444` | Price down, losing trade, bearish signal |
| `loss-subtle` | `#450A0A` | Background for loss cells |
| `warning` | `#F59E0B` | Caution, medium risk, watch state |
| `warning-subtle` | `#451A03` | Background for warning states |
| `neutral` | `#64748B` | No signal, loading, disabled |

### Module Accent Colors
Each module has a unique accent for instant recognition.

| Module | Color | Hex |
|--------|-------|-----|
| RadarX | Blue | `#3B82F6` |
| WhaleRadar | Cyan | `#06B6D4` |
| LiquidMap | Orange | `#F97316` |
| SentimentPulse | Purple | `#A855F7` |
| MacroPulse | Indigo | `#6366F1` |
| GemRadar | Emerald | `#10B981` |
| RiskCalc | Yellow | `#EAB308` |
| TradeLog | Slate | `#94A3B8` |
| PerformanceCore | Teal | `#14B8A6` |
| Oracle | Violet | `#8B5CF6` |

### Text Scale
| Token | Hex | Usage |
|-------|-----|-------|
| `text-primary` | `#F1F5F9` | Headlines, primary content |
| `text-secondary` | `#94A3B8` | Labels, secondary content |
| `text-muted` | `#64748B` | Placeholders, timestamps, meta |
| `text-disabled` | `#374151` | Disabled elements |

### Border Scale
| Token | Hex | Usage |
|-------|-----|-------|
| `border-subtle` | `#1E293B` | Card borders, dividers |
| `border-default` | `#334155` | Input borders, table rows |
| `border-strong` | `#475569` | Active inputs, focused states |

---

## 2. Typography

### Font Families
```css
/* Interface font — all UI text */
font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;

/* Data font — prices, numbers, percentages, code */
font-family: 'JetBrains Mono', 'Fira Code', monospace;
```

Both fonts loaded from Google Fonts. Inter for readability at all sizes, JetBrains Mono for tabular number alignment (critical for price tables).

### Type Scale
| Token | Size | Weight | Line Height | Usage |
|-------|------|--------|-------------|-------|
| `text-xs` | 11px | 400 | 1.4 | Timestamps, meta labels |
| `text-sm` | 13px | 400 | 1.5 | Table cells, secondary content |
| `text-base` | 14px | 400 | 1.6 | Body text, descriptions |
| `text-md` | 16px | 500 | 1.5 | Card titles, section labels |
| `text-lg` | 18px | 600 | 1.4 | Page section headings |
| `text-xl` | 22px | 700 | 1.3 | Page titles, big metrics |
| `text-2xl` | 28px | 700 | 1.2 | Hero numbers (P&L, score) |
| `text-3xl` | 36px | 800 | 1.1 | Oracle score display |

### Data Typography Rules
- All prices, percentages, and quantities always use `JetBrains Mono`
- Positive numbers: `profit` color, prefixed with `+`
- Negative numbers: `loss` color, prefixed with `−` (not a hyphen)
- Numbers right-aligned in all tables
- Percentage changes always show 2 decimal places: `+4.21%`

---

## 3. Spacing System

8-point grid. All spacing values are multiples of 4px.

| Token | Value | Usage |
|-------|-------|-------|
| `space-1` | 4px | Icon padding, tight gaps |
| `space-2` | 8px | Element internal padding |
| `space-3` | 12px | Small component padding |
| `space-4` | 16px | Standard padding (cards, buttons) |
| `space-5` | 20px | Section inner padding |
| `space-6` | 24px | Card padding |
| `space-8` | 32px | Section gaps |
| `space-10` | 40px | Page section spacing |
| `space-12` | 48px | Large section breaks |

### Border Radius
| Token | Value | Usage |
|-------|-------|-------|
| `radius-sm` | 4px | Badges, tags, small chips |
| `radius-md` | 8px | Buttons, inputs, small cards |
| `radius-lg` | 12px | Cards, panels |
| `radius-xl` | 16px | Modals, large panels |

---

## 4. Layout System

### Global Layout Structure
```
┌─────────────────────────────────────────────────────┐
│  TOP BAR — Macro snapshot + account + notifications  │  48px
├──────────┬──────────────────────────────────────────┤
│          │                                          │
│ SIDEBAR  │           MAIN CONTENT                   │
│          │                                          │
│  240px   │     Fluid (fills remaining width)         │
│          │                                          │
│ (64px    │                                          │
│  when    │                                          │
│  collapsed)                                         │
└──────────┴──────────────────────────────────────────┘
```

### Top Bar (48px fixed)
Always visible across all modules. Contains:
- TradeCore logo (left)
- Macro pulse strip: DXY · US10Y · VIX · BTC ETF Flows (center)
- Account balance · Notifications bell · User avatar (right)

### Sidebar (240px / 64px collapsed)
- Module navigation with icon + label
- Each module uses its accent color when active
- Collapsible to icon-only mode for more screen space
- Bottom: Settings, Billing, Help

### Main Content
- Padding: 24px all sides
- Max width: none (full width for data density)
- Scrollable per module, top bar and sidebar fixed

### Grid System
12-column grid within main content.
- 1 col = ~80px at 1280px viewport
- Gutters: 16px
- Standard card widths: 3col (25%), 4col (33%), 6col (50%), 12col (100%)

---

## 5. Core Components

### Cards
The primary container for all module content.

```
┌─────────────────────────────────────────┐
│ Card Header                             │  Padding: 16px
│ Title · Subtitle · Actions              │
├─────────────────────────────────────────┤
│                                         │
│ Card Body                               │  Padding: 0 or 16px
│                                         │
└─────────────────────────────────────────┘

Background:   bg-card (#1A2235)
Border:       1px solid border-subtle (#1E293B)
Border-radius: radius-lg (12px)
Shadow:       0 4px 24px rgba(0,0,0,0.3)
```

**Card variants:**
- `default` — standard card
- `alert` — left border 3px in module accent color
- `active` — border in primary-glow, slight glow shadow
- `danger` — left border 3px loss color

### Buttons

**Primary**
```
Background: primary-500 (#3B82F6)
Text: white, 14px, weight 600
Padding: 8px 16px
Radius: radius-md (8px)
Hover: primary-400 + slight upward translate
Active: primary-600
```

**Secondary**
```
Background: bg-elevated
Border: 1px solid border-default
Text: text-primary, 14px, weight 500
Hover: bg-hover
```

**Ghost**
```
Background: transparent
Text: text-secondary
Hover: bg-hover, text-primary
```

**Danger**
```
Background: loss (#EF4444)
Text: white
Used for: stop-loss actions, cancel trades, delete
```

**Sizes:** `sm` (32px height), `md` (36px), `lg` (44px)

### Badges / Tags
Inline status indicators.

```
Padding: 2px 8px
Radius: radius-sm (4px)
Font: text-xs (11px), weight 600, uppercase
```

| Type | Background | Text |
|------|-----------|------|
| `bullish` | profit-subtle | profit |
| `bearish` | loss-subtle | loss |
| `warning` | warning-subtle | warning |
| `neutral` | bg-elevated | text-muted |
| `module` | module accent subtle | module accent |

### Tables
Data tables appear in RadarX, WhaleRadar, GemRadar, TradeLog.

```
Header row:   bg-secondary, text-muted, text-xs uppercase, weight 600
Body rows:    bg-card, border-bottom border-subtle
Hover row:    bg-hover
Selected row: bg-primary-subtle, border-left 2px primary-500

Row height:   48px (comfortable) or 36px (dense mode)
Cell padding: 12px 16px
```

Rules:
- Numbers always right-aligned
- Text always left-aligned
- Sortable columns show sort icon on hover
- Sticky header on scroll

### Inputs & Forms
```
Background:   bg-secondary
Border:       1px solid border-default
Border-radius: radius-md (8px)
Height:       40px
Padding:      0 12px
Font:         14px, text-primary
Placeholder:  text-muted

Focus:        border-strong + primary-glow box-shadow
Error:        border loss color + error message below
```

### Alert Feed Items
Used in RadarX, WhaleRadar, GemRadar, Oracle alert feeds.

```
┌─ Module color bar (3px) ──────────────────────────────┐
│  🚨 SYMBOL   Badge    Z-score / Ratio    Timestamp    │
│  Price change · Volume · Context line                  │
│  [Action button]                          [TradingView]│
└───────────────────────────────────────────────────────┘

New alert: slides in from top with fade
Unread: slightly brighter background
Read: normal card opacity
```

### Metric Cards
Small cards showing a single KPI — used in top bar and dashboards.

```
┌──────────────────────┐
│ LABEL           ↗    │
│ 4.21%                │
│ +0.12% today         │
└──────────────────────┘

Value font:  JetBrains Mono, text-xl, colored by direction
Label font:  Inter, text-xs, text-muted, uppercase
```

### Oracle Score Display
The signature component — large radial score.

```
Outer ring:   Colored arc (green = bullish, red = bearish)
Center:       Score number (36px, JetBrains Mono, bold)
Below:        Recommendation label
Around:       6 module signal dots (filled = bullish, empty = neutral, X = bearish)
```

---

## 6. Data Visualization

### Price Charts
Using TradingView Lightweight Charts library.

```
Background:     bg-canvas
Grid lines:     border-subtle, 1px, dashed
Candle up:      profit (#10B981)
Candle down:    loss (#EF4444)
Volume bars:    profit/loss at 30% opacity
Crosshair:      text-muted, 1px dashed
```

### Volume Bars
```
Normal volume:    primary-500 at 40% opacity
Spike volume:     primary-500 at 100% opacity + glow
```

### Liquidation Heatmap
```
Low concentration:  bg-card
Medium:             warning at 30% → 60% opacity
High:               warning → loss gradient
Extreme:            loss at 100% + pulsing glow
```

### Funding Rate Bars
```
Positive (longs pay):  profit, bar extends right
Negative (shorts pay): loss, bar extends left
Neutral:               neutral, centered dot
```

### Equity Curve
```
Line:       primary-500, 2px
Fill below: primary-500 to transparent gradient
Drawdown:   loss at 20% opacity fill
```

---

## 7. Animation & Motion

**Philosophy:** motion communicates change, never decorates. Every animation has a purpose.

| Event | Animation | Duration |
|-------|-----------|----------|
| New alert arrives | Slide in from top + fade | 300ms ease-out |
| Price update | Number flip (count up/down) | 150ms |
| Alert highlight | Background flash then fade | 500ms |
| Card hover | Y translate −2px | 150ms ease |
| Page transition | Fade | 200ms |
| Modal open | Scale 0.96 → 1 + fade | 200ms ease-out |
| Live dot pulse | Scale pulse 1 → 1.3 → 1 | 2s loop |
| Oracle score | Arc draw on load | 600ms ease-out |

**Rule:** never animate layout (no moving columns, no sliding panels while data is present). Only animate opacity, transform, and color.

---

## 8. Icon System

Using **Lucide Icons** — clean, consistent, 24px grid.

| Context | Size | Stroke |
|---------|------|--------|
| Navigation sidebar | 20px | 1.5px |
| Card actions | 16px | 1.5px |
| Inline with text | 14px | 1.5px |
| Hero / empty states | 48px | 1px |

Key icons per module:
- RadarX → `radar` / `activity`
- WhaleRadar → `anchor` / `waves`
- LiquidMap → `flame` / `zap`
- SentimentPulse → `heart-pulse`
- MacroPulse → `globe` / `trending-up`
- GemRadar → `gem` / `sparkles`
- RiskCalc → `shield` / `calculator`
- TradeLog → `book-open` / `pen-line`
- PerformanceCore → `bar-chart-2`
- Oracle → `eye` / `cpu`

---

## 9. States

Every interactive element must handle all states:

| State | Treatment |
|-------|-----------|
| Default | As designed |
| Hover | Subtle brighten + cursor pointer |
| Active / Pressed | Slight darken + scale 0.98 |
| Focus | Primary-glow outline 2px |
| Disabled | 40% opacity, cursor not-allowed |
| Loading | Skeleton shimmer (bg-secondary → bg-elevated → bg-secondary) |
| Empty | Centered icon + message + optional CTA |
| Error | Loss color border + error message |

### Skeleton Loading
All cards show skeleton state before data loads.
```
Color A: bg-card
Color B: bg-elevated
Animation: shimmer left to right, 1.5s loop
```

---

## 10. Responsive Breakpoints

TradeCore is desktop-first — traders use large monitors. Mobile is a secondary concern (Telegram handles mobile alerts).

| Breakpoint | Width | Behavior |
|------------|-------|----------|
| `xl` | 1280px+ | Full layout, sidebar expanded |
| `lg` | 1024–1279px | Sidebar collapsed to icons |
| `md` | 768–1023px | Sidebar hidden, hamburger menu |
| `sm` | < 768px | Single column, simplified tables |

---

## Summary Tokens (CSS Variables)

```css
:root {
  /* Backgrounds */
  --bg-canvas:     #070C18;
  --bg-primary:    #0B1120;
  --bg-secondary:  #111827;
  --bg-card:       #1A2235;
  --bg-elevated:   #1F2D42;
  --bg-hover:      #243350;

  /* Brand */
  --primary:       #3B82F6;
  --primary-hover: #60A5FA;
  --primary-active:#2563EB;
  --primary-subtle:#1E3A5F;
  --primary-glow:  rgba(59,130,246,0.15);

  /* Semantic */
  --profit:        #10B981;
  --profit-subtle: #064E3B;
  --loss:          #EF4444;
  --loss-subtle:   #450A0A;
  --warning:       #F59E0B;
  --warning-subtle:#451A03;

  /* Text */
  --text-primary:  #F1F5F9;
  --text-secondary:#94A3B8;
  --text-muted:    #64748B;
  --text-disabled: #374151;

  /* Borders */
  --border-subtle: #1E293B;
  --border-default:#334155;
  --border-strong: #475569;

  /* Module accents */
  --radarx:        #3B82F6;
  --whaleradar:    #06B6D4;
  --liquidmap:     #F97316;
  --sentimentpulse:#A855F7;
  --macropulse:    #6366F1;
  --gemradar:      #10B981;
  --riskcalc:      #EAB308;
  --tradelog:      #94A3B8;
  --performancecore:#14B8A6;
  --oracle:        #8B5CF6;

  /* Typography */
  --font-ui:   'Inter', sans-serif;
  --font-data: 'JetBrains Mono', monospace;

  /* Spacing */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;

  /* Radius */
  --radius-sm: 4px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --radius-xl: 16px;
}
```
