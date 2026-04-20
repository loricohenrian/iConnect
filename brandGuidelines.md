# brandGuidelines.md — iConnect Brand & Design Guidelines

## Brand Identity

**Product Name:** iConnect
**Tagline:** *"Affordable Internet, Smart Monitoring"*
**Type:** School-based coin-operated WiFi system
**Target Users:**
- Primary: Students (ages 14–25)
- Secondary: School administrators and staff

---

## Brand Personality

| Trait | Description |
|---|---|
| Trustworthy | Students trust the system with their coins — every peso is accounted for |
| Simple | Easy to use — no tech knowledge required from students |
| Modern | Feels like a real product, not a school project |
| Transparent | Students always know their remaining time, speed, and cost |
| Professional | Admin side feels like a real business dashboard |

---

## Color Palette

### Primary Colors

| Name | Hex | Usage |
|---|---|---|
| iConnect Blue | `#1A73E8` | Primary buttons, active states, links, highlights |
| iConnect Dark | `#1E293B` | Navigation backgrounds, headers, admin sidebar |
| iConnect White | `#FFFFFF` | Page backgrounds, card backgrounds |

### Secondary Colors

| Name | Hex | Usage |
|---|---|---|
| Success Green | `#10B981` | Connected status, successful payment, positive indicators |
| Warning Amber | `#F59E0B` | Low time warning, revenue alerts, caution states |
| Danger Red | `#EF4444` | Disconnected, errors, low revenue alerts |
| Info Teal | `#06B6D4` | Informational banners, band recommendation notices |
| Neutral Gray | `#64748B` | Secondary text, borders, inactive states |
| Light Gray | `#F1F5F9` | Page backgrounds, table rows, subtle sections |

### Gradient (use sparingly)
```css
/* Hero sections and key stat cards only */
background: linear-gradient(135deg, #1A73E8 0%, #0EA5E9 100%);
```

---

## Typography

### Font Stack
```css
font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
```

### Font Sizes

| Element | Size | Weight |
|---|---|---|
| Page title (H1) | 28px | 700 Bold |
| Section title (H2) | 22px | 600 SemiBold |
| Card title (H3) | 18px | 600 SemiBold |
| Body text | 16px | 400 Regular |
| Small text / labels | 14px | 400 Regular |
| Tiny / captions | 12px | 400 Regular |

### Font Rules
- Never use font sizes below 12px
- Use font weight 700 only for page titles and key metrics
- Use font weight 600 for section headings and card titles
- Use font weight 400 for all body text
- All text must be readable on both light and dark backgrounds

---

## Spacing System

```css
--spacing-xs:  4px
--spacing-sm:  8px
--spacing-md:  16px
--spacing-lg:  24px
--spacing-xl:  32px
--spacing-2xl: 48px
--spacing-3xl: 64px
```

---

## Border Radius

```css
--radius-sm:   6px   /* buttons, inputs, small badges */
--radius-md:   10px  /* cards, panels */
--radius-lg:   16px  /* modal boxes, feature cards */
--radius-full: 9999px /* pills, status badges */
```

---

## Component Design

### Buttons

**Primary Button** — main actions (Pay, Connect, Submit)
```css
background: #1A73E8;
color: #FFFFFF;
border-radius: 8px;
padding: 12px 24px;
font-weight: 600;
font-size: 16px;
border: none;
cursor: pointer;
transition: background 0.2s;

/* Hover */
background: #1557B0;
```

**Secondary Button** — secondary actions (Cancel, Back)
```css
background: transparent;
color: #1A73E8;
border: 2px solid #1A73E8;
border-radius: 8px;
padding: 10px 22px;
font-weight: 600;
```

**Danger Button** — destructive actions (Delete, Remove)
```css
background: #EF4444;
color: #FFFFFF;
border-radius: 8px;
padding: 12px 24px;
```

**Success Button** — confirmation actions
```css
background: #10B981;
color: #FFFFFF;
border-radius: 8px;
padding: 12px 24px;
```

---

### Cards

```css
background: #FFFFFF;
border-radius: 12px;
padding: 24px;
box-shadow: 0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04);
border: 1px solid #E2E8F0;
```

**Stat Cards** (revenue, users, etc.)
```css
/* Use primary gradient for key metrics */
background: linear-gradient(135deg, #1A73E8, #0EA5E9);
color: #FFFFFF;
border-radius: 12px;
padding: 24px;
```

---

### Status Badges

```css
/* Connected / Active */
background: #D1FAE5;
color: #065F46;
border-radius: 9999px;
padding: 4px 12px;
font-size: 13px;
font-weight: 600;

/* Disconnected / Expired */
background: #FEE2E2;
color: #991B1B;

/* Warning / Low time */
background: #FEF3C7;
color: #92400E;

/* Paused */
background: #E0E7FF;
color: #3730A3;
```

---

### Form Inputs

```css
border: 1.5px solid #CBD5E1;
border-radius: 8px;
padding: 10px 14px;
font-size: 15px;
color: #1E293B;
background: #FFFFFF;
width: 100%;
transition: border-color 0.2s;

/* Focus state */
border-color: #1A73E8;
outline: none;
box-shadow: 0 0 0 3px rgba(26, 115, 232, 0.15);
```

---

## User Side (Captive Portal) Design Guidelines

### Layout
- Single column, centered layout — maximum width 480px on desktop
- Mobile-first design — most students use phones
- Large tap targets — minimum 48px height for all interactive elements
- Avoid clutter — show only what the student needs right now

### Portal Pages

**Plan Selection Screen**
- Show plans as large cards, easy to tap
- Display price prominently in large bold text (₱5, ₱10, ₱20)
- Show duration clearly (30 mins, 1 hour, 2 hours)
- Show speed if applicable
- Use iConnect Blue as the selected plan highlight color

**Session Timer Page**
- Timer is the most important element — display it large (min 48px font)
- Show remaining time in HH:MM:SS format
- Use color progression: Green (>10 min) → Amber (5–10 min) → Red (<5 min)
- Show current internet speed below timer
- Show "Extend Session" button prominently
- Announcement banner at top if active — use Info Teal background

**Announcement Banner**
```css
background: #FEF3C7;   /* amber for warnings */
border-left: 4px solid #F59E0B;
padding: 12px 16px;
border-radius: 8px;
font-size: 14px;
color: #92400E;
```
For connectivity issues use red variant:
```css
background: #FEE2E2;
border-left: 4px solid #EF4444;
color: #991B1B;
```

**Band Recommendation Notice**
```css
background: #E0F2FE;
border-left: 4px solid #06B6D4;
padding: 12px 16px;
border-radius: 8px;
color: #0C4A6E;
font-size: 14px;
```

**5-Minute Warning Alert**
```css
background: #FEF3C7;
border: 2px solid #F59E0B;
border-radius: 12px;
padding: 16px;
text-align: center;
animation: pulse 1s infinite;
```

---

## Admin Side (Dashboard) Design Guidelines

### Layout
- Sidebar navigation — fixed left, 240px wide
- Dark sidebar (`#1E293B`) with white text
- Main content area — light gray background (`#F1F5F9`)
- Top navigation bar — white with shadow
- Responsive — collapses to hamburger on mobile

### Sidebar Navigation
```css
/* Sidebar */
background: #1E293B;
width: 240px;
height: 100vh;
position: fixed;

/* Nav items */
color: #94A3B8;
padding: 10px 16px;
border-radius: 8px;
font-size: 14px;
font-weight: 500;

/* Active nav item */
background: rgba(26, 115, 232, 0.15);
color: #1A73E8;
border-left: 3px solid #1A73E8;
```

### Dashboard Layout — Stat Cards Row
```
[Revenue Today] [Connected Users] [Bandwidth Used] [ROI Progress]
     ↓ gradient blue    ↓ green         ↓ teal           ↓ amber
```

### Charts and Data Visualization

**Revenue Bar Chart**
- Color: iConnect Blue `#1A73E8`
- Background: White card
- Grid lines: Light gray `#E2E8F0`
- Use Chart.js

**Peak Hours Heatmap**
- Low activity: `#EFF6FF` (lightest blue)
- Medium activity: `#93C5FD`
- High activity: `#1A73E8`
- Very high activity: `#1557B0` (darkest)
- Cells: 40px × 40px minimum
- Labels: 12px gray text

**ROI Progress Bar**
```css
/* Track */
background: #E2E8F0;
border-radius: 9999px;
height: 12px;

/* Fill */
background: linear-gradient(90deg, #10B981, #1A73E8);
border-radius: 9999px;
transition: width 0.5s ease;
```

**Revenue Goal Progress**
- Under target: Amber `#F59E0B`
- On track: Blue `#1A73E8`
- Target exceeded: Green `#10B981`

---

## Icons

Use **Bootstrap Icons** or **Heroicons** — both free and clean.

| Icon | Usage |
|---|---|
| `bi-wifi` | WiFi connection indicators |
| `bi-coin` | Revenue, coin-related features |
| `bi-people` | Connected users |
| `bi-clock` | Session timer, time-related |
| `bi-graph-up` | Revenue growth, analytics |
| `bi-bar-chart` | Revenue charts |
| `bi-bell` | Notifications, announcements |
| `bi-shield-check` | Security, whitelist |
| `bi-download` | Export, download reports |
| `bi-gear` | Settings, configuration |
| `bi-thermometer` | Temperature monitoring |
| `bi-battery-charging` | Solar power |

---

## Responsive Breakpoints

```css
/* Mobile first */
/* Default styles target mobile (< 640px) */

/* Tablet */
@media (min-width: 640px) { }

/* Desktop */
@media (min-width: 1024px) { }

/* Large desktop */
@media (min-width: 1280px) { }
```

---

## Writing Style & Tone

### For Students (Captive Portal)
- Simple, friendly language
- Short sentences
- Filipino-English mix is acceptable for labels
- Examples:
  - âœ… "Insert coins to get started"
  - âœ… "Your time is almost up!"
  - âœ… "Connected! Enjoy browsing."
  - âŒ "Authentication session initiated"
  - âŒ "Network access granted upon successful payment"

### For Admin (Dashboard)
- Clear, professional language
- Data-focused — let numbers speak
- Action-oriented labels
- Examples:
  - âœ… "Revenue Today"
  - âœ… "Export Report"
  - âœ… "Post Announcement"
  - âŒ "Monetary Accumulation for Current Date"

---

## Currency Display

Always display Philippine Peso using the ₱ symbol:

```
âœ… ₱5
âœ… ₱1,250.00
âœ… ₱0.50/min
âŒ PHP 5
âŒ P5
âŒ 5 pesos
```

For large amounts use comma separator:
```
₱1,000
₱12,500
₱125,000
```

---

## Time Display

| Context | Format | Example |
|---|---|---|
| Session countdown | HH:MM:SS | 01:25:30 |
| Session duration | X hours Y mins | 1 hour 30 mins |
| Timestamp | MMM DD, YYYY HH:MM | Apr 09, 2026 10:30 |
| Date only | MMM DD, YYYY | Apr 09, 2026 |
| Time only | HH:MM AM/PM | 10:30 AM |

All times in **Asia/Manila** timezone (GMT+8).

---

## Error States

**Empty state (no data)**
```html
<div class="empty-state">
  <icon size="48px" color="#CBD5E1" />
  <p>No data available yet</p>
  <small>Data will appear here once students start using the system</small>
</div>
```

**Error message**
```css
background: #FEE2E2;
border: 1px solid #FECACA;
border-radius: 8px;
padding: 12px 16px;
color: #991B1B;
font-size: 14px;
```

**Success message**
```css
background: #D1FAE5;
border: 1px solid #A7F3D0;
border-radius: 8px;
padding: 12px 16px;
color: #065F46;
font-size: 14px;
```

---

## Loading States

- Use skeleton loaders for dashboard cards — never blank white boxes
- Show spinner for actions that take time (generating reports, exporting)
- Spinner color: iConnect Blue `#1A73E8`

```css
/* Spinner */
border: 3px solid #E2E8F0;
border-top: 3px solid #1A73E8;
border-radius: 50%;
width: 24px;
height: 24px;
animation: spin 0.8s linear infinite;
```

---

## Do's and Don'ts

### Do
- Keep the captive portal minimal — students just want to connect fast
- Make the session timer the most visible element on the portal
- Use large, clear numbers for peso amounts and time remaining
- Always show connection status clearly
- Test all pages on a phone screen first

### Don't
- Never use red for anything other than errors/warnings
- Never hide important information like remaining time or price
- Never use more than 3 colors on a single page
- Never use fonts smaller than 12px
- Never auto-play sounds without user interaction
- Never make students scroll to find the pay button

---

## CSS Variables (copy this to your base stylesheet)

```css
:root {
  /* Colors */
  --color-primary:     #1A73E8;
  --color-primary-dark: #1557B0;
  --color-dark:        #1E293B;
  --color-success:     #10B981;
  --color-warning:     #F59E0B;
  --color-danger:      #EF4444;
  --color-info:        #06B6D4;
  --color-gray:        #64748B;
  --color-light:       #F1F5F9;
  --color-white:       #FFFFFF;
  --color-border:      #E2E8F0;

  /* Typography */
  --font-family:       'Inter', 'Segoe UI', system-ui, sans-serif;
  --font-size-xs:      12px;
  --font-size-sm:      14px;
  --font-size-md:      16px;
  --font-size-lg:      18px;
  --font-size-xl:      22px;
  --font-size-2xl:     28px;

  /* Spacing */
  --space-xs:   4px;
  --space-sm:   8px;
  --space-md:   16px;
  --space-lg:   24px;
  --space-xl:   32px;
  --space-2xl:  48px;

  /* Border radius */
  --radius-sm:   6px;
  --radius-md:   10px;
  --radius-lg:   16px;
  --radius-full: 9999px;

  /* Shadows */
  --shadow-sm: 0 1px 3px rgba(0,0,0,0.08);
  --shadow-md: 0 4px 6px rgba(0,0,0,0.07);
  --shadow-lg: 0 10px 15px rgba(0,0,0,0.10);
}
```


