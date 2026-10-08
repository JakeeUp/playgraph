# Arcade Darkroom design system

The reference for the app's theme. The live values are the tokens in
`app/static/styles.css`; nothing in `app/` loads these files. The app has the
colors, type, cover grid, top header, two-column game page, hours blocks and
review rows. The cover hover controls and status strip wait on Phase 4 tracking.

| File | What it is |
|---|---|
| `tailwind.config.js` | Tailwind v3 theme: `game-*` colours, type scale, tight spacing, radii, two allowed shadows |
| `base.css` | Plain-CSS baseline: font faces, the same tokens as custom properties, scrollbars, links, covers, hours block, half-star ratings |
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

- **A game shelf, not a landing page.** Cover art and real numbers carry the color. The chrome
  around them stays quiet, so the art is always the brightest thing on screen.
- **Density first.** 7 covers per row on desktop, 16px gaps, 13 to 15px body, 11 to 12px metadata.
- **Rules, not boxes.** Group things with a 1px `rgba(255,255,255,.08)` rule or a slightly lighter
  surface. Section titles sit on a full-width rule. Review rows are split by hairlines, never boxed.
- **Two shadows only.** `cover` for box art sitting on the canvas, `float` for dialogs and menus.
  Cards, buttons and panels get none.
- **Cartridge corners.** 3px on covers and badges, 4px on buttons, inputs and chips, 6px on panels
  and dialogs. Only avatars are round.
- **Real content over decoration.** The signed-out front page shows the latest real reviews, not a
  list of features. When something needs atmosphere, it comes from game art.

## What this system doesn't do

These are the defaults that make a site read as generated. Each one was tried in this repo and
taken back out (see the October 2026 commits), so don't reintroduce them without a reason:

- Gradient text, or orange-to-pink gradients on buttons. The orange is one flat colour.
- Glows: accent-coloured `box-shadow`, `drop-shadow` on the logo, radial blobs behind sections.
- Frosted glass (`backdrop-filter`) on the header, badges or anything else.
- Uppercase, letter-spaced "eyebrow" labels above headings.
- Rows of three feature cards with icons in tinted rounded squares.
- Pill-shaped buttons and chips.
- Cards that fade or slide up as they load, and hover effects that move whole cards.
- Generic copy. Write the sentence only this site could say ("two hundred hours in, or twenty
  minutes before a refund?"), not "Track, review and discover".

The one allowed gradient job is art fading into the canvas: the library hero, the game page
hero band and the cover wall on the front page.

## Colour

| Token | Hex | Use |
|---|---|---|
| `game-bg` | `#0f1317` | Canvas |
| `game-surface` | `#171d23` | Panels, inputs, the cover placeholder |
| `game-raised` | `#20282f` | Popovers, menus, platform badges, secondary buttons |
| `game-text` | `#ffffff` | Headings, titles, values, the selected filter chip |
| `game-body` | `#a3b4c2` | Running text |
| `game-muted` | `#728a9c` | Labels, timestamps, counts |
| `game-accent` | `#ff7a00` | Primary action, links, active tab, rating stars, focus ring |
| `game-playing` | `#00b4d8` | In progress, and the one chart series |
| `game-done` | `#00e054` | Verified Steam hours, completed / 100% |
| `game-done-ink` | `#04210f` | Text on a solid green hours block |
| `game-fave` | `#ff3366` | Favorite heart only |

Muted is about 5:1 on the canvas, which clears AA for 11px text. Each status colour has one
meaning. A selected filter chip is white with dark text, so orange stays reserved for actions.

**Per-game tint.** The game page samples its cover (Steam's CDN sends
`Access-Control-Allow-Origin: *`), weights the pixels by saturation, darkens the result and sets
it as `--tint`. It colours the 3px line along the top of the dialog and the fade under the hero
art. When sampling fails, both fall back to the surface colours.

## Type

- **Headings:** Archivo, variable, set at `font-stretch: 84%` and weight 800 (900 for the
  signed-out headline). Narrow and heavy, like box art and scoreboards. Sentence case, never
  uppercase or tracked wide. Hierarchy comes from size and weight alone.
- **Interface and body:** IBM Plex Sans.
- **Numbers:** IBM Plex Mono with tabular figures (`.num`), so hours and counts line up. The
  stat strip, hours blocks, dates and counts all use it.

Scale: `tag` 10 · `meta` 11 · `body` 13 · `ui` 14 · `title` 18 · `head` 24 · `hero` 42 ·
`display` 76 (front page headline only, `clamp(40px, 6vw, 76px)` in the app).

Space Grotesk was the heading face until October 2026. It was dropped because it has become the
default for generated sites.

## Patterns

- **Hours block.** Verified Steam playtime is a solid green block with dark mono text
  (`.hours-block` in the app, `.hours-block` in `base.css`). It's the loudest element in the system
  on purpose. Unverified hours stay as plain muted text.
- **Cover grid.** Columns go 3, 4, 5, 6, 7 as the viewport widens. Covers are 2:3, or 4:3 when the
  cartridge toggle is on. Hover lifts the cover 3px and brightens it, with no scaling, ring or glow.
  The play, heart and list controls appear on hover and on keyboard focus.
- **Stat strip.** Library numbers sit in one strip between two rules, split by hairlines. Big mono
  values, small muted labels, no icons.
- **Section heading.** Title on the left, controls on the right, a full-width `line-strong` rule
  under both.
- **Review row.** A 32px avatar, a one-line meta row (name, stars, platform badge, hours block,
  age), the text, then like and reply counts. A 36px cover thumbnail sits on the right.
  Rows are separated by a 1px divider only.
- **Detail dashboard.** A hero band of the game's wide art fades into the dialog, with the cover
  overlapping its bottom edge. The left column has the art, your hours, actions and links. The
  right column has the title, a reference-manual `dl` (label column plus value column, hairline
  rows), platform capsules and tabs with an orange underline on the active one.

## In the app

Tailwind isn't used there: the app has no build step. The `game-*` values live
in the token block of `app/static/styles.css`. All fonts are self-hosted in
`app/static/fonts` (licenses in `LICENSE.txt` there), because the CSP only
allows fonts from the app's own origin.
