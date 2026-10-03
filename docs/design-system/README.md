# Arcade Darkroom design system

The reference for the app's theme. The live values are the tokens in
`app/static/styles.css`; nothing in `app/` loads these files. The app has the
colors, type, cover grid, top header, two-column game page and compact review
rows. The cover hover controls and status strip wait on Phase 4 tracking.

| File | What it is |
|---|---|
| `tailwind.config.js` | Tailwind v3 theme: `game-*` colours, type scale, tight spacing, radii, no shadows |
| `base.css` | Plain-CSS baseline: font faces, the same tokens as custom properties, scrollbars, links, covers, half-star ratings |
| `templates.html` | Header, 7-column cover grid with inline logging controls, detail dashboard, review timeline |

## Preview

```
cd docs/design-system
python -m http.server 8765 --bind 127.0.0.1
```

Open **`http://127.0.0.1:8765/templates.html`** and type the `http://` yourself.
Python's server doesn't do HTTPS, and a browser that tries it gets `code 400`
in the server log. If you started the server from `docs/` instead, the page is
at `/design-system/templates.html`.

Only the preview uses the Tailwind Play CDN and Google Fonts. The 404s for
`/assets/fonts/*` there are expected, because those paths exist only in the app.

## Principles

- **Density first.** 7 covers per row on desktop, 14px gaps, 13px body, 10 to 11px metadata.
- **The art is the hero.** The canvas is dark and low-contrast. Covers sit on it with no box around them.
- **Flat planes.** Group things with a lighter surface or a 1px `rgba(255,255,255,.06)` rule.
  The theme has no box-shadow utilities, so none can be added by accident.
- **Cartridge corners.** 3px on covers, inputs and badges; 4px on buttons. Only avatars are round.

## Colour

| Token | Hex | Use |
|---|---|---|
| `game-bg` | `#101418` | Canvas |
| `game-surface` | `#182026` | Sections, stat blocks, inputs |
| `game-raised` | `#222c36` | Popovers, menus, platform badges, the active filter |
| `game-text` | `#ffffff` | Headings, titles, values |
| `game-body` | `#9bb0c1` | Running text |
| `game-muted` | `#6e889d` | Labels, hours, timestamps |
| `game-accent` | `#ff7a00` | Primary action, played, rating stars, focus ring |
| `game-playing` | `#00b4d8` | In progress |
| `game-done` | `#00e054` | Completed / 100% |
| `game-fave` | `#ff3366` | Favorite heart only |

The brief's muted grey `#627d93` is 4.3:1 on the canvas, which is below AA for
11px text, so the token is `#6e889d` (about 4.9:1). Each status colour has one
meaning. It shows as a 2px strip under the cover and as the colour of the
pressed control.

## Type

- **Headings:** Space Grotesk 700, `-0.02em`, sentence case. Never uppercase or tracked wide.
  Hierarchy comes from size and weight alone.
- **Interface and body:** IBM Plex Sans, which is already self-hosted in the app.
- **Numbers:** IBM Plex Mono with tabular figures (`.num`), so hours and counts line up.

Scale: `tag` 10 · `meta` 11 · `body` 13 · `ui` 14 · `title` 17 · `head` 22 · `hero` 34.

## Patterns

- **Cover grid.** Columns go 3, 4, 5, 6, 7 as the viewport widens. Covers are 2:3, or 4:3 when the
  cartridge toggle is on. Hover only raises brightness, with no scaling. The play, heart and list
  controls appear on hover and on keyboard focus.
- **Review row.** A 32px avatar, a one-line meta row (name, stars, platform badge, hours and
  status, age), the text, then like and reply counts. A 36px cover thumbnail sits on the right.
  Rows are separated by a 1px divider only.
- **Detail dashboard.** The left column has the art, a status and hours block, actions and the
  average rating. The right column has year and studio, the title, a short blurb, a
  reference-manual `dl` (label column plus value column, hairline rows), platform capsules and tabs.

## In the app

Tailwind isn't used there: the app has no build step. The `game-*` values live
in the token block of `app/static/styles.css`. Space Grotesk is self-hosted in
`app/static/fonts`, because the CSP only allows fonts from the app's own origin.
