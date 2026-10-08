// PlayGraph "Arcade Darkroom" theme, Tailwind v3. Mirrors the token block in
// app/static/styles.css, which is the source of truth.
// JSON-compatible: no functions or plugins, so the same object can be pasted
// into `tailwind.config = {...}` for the Play CDN or loaded by the CLI.
module.exports = {
  content: ["./**/*.html", "./**/*.js"],
  theme: {
    // Replaced, not extended. Only things that really float get a shadow:
    // covers sitting on the canvas, and dialogs or menus. Cards never do.
    boxShadow: {
      none: "none",
      cover: "0 1px 2px rgba(0,0,0,0.4), 0 8px 18px -10px rgba(0,0,0,0.7)",
      float: "0 24px 60px -20px rgba(0,0,0,0.85)",
    },
    borderRadius: {
      none: "0",
      sm: "2px",
      DEFAULT: "3px",   // covers, inputs, badges
      md: "4px",        // buttons, chips, popovers
      lg: "6px",        // panels, dialogs
      full: "9999px",   // avatars only, never buttons or chips
    },
    extend: {
      colors: {
        game: {
          bg: "#0f1317",          // canvas
          surface: "#171d23",     // sections, sidebars, list backdrops
          raised: "#20282f",      // popovers, menus, filters
          line: "rgba(255,255,255,0.08)",
          "line-strong": "rgba(255,255,255,0.16)",
          text: "#ffffff",        // headings
          body: "#a3b4c2",        // running text
          muted: "#728a9c",       // metadata, about 5:1 on the canvas
          accent: "#ff7a00",      // played, primary action, rating stars
          "accent-hover": "#ff9433",
          playing: "#00b4d8",     // in progress
          done: "#00e054",        // verified Steam hours, completed / 100%
          "done-ink": "#04210f",  // text on a solid green hours block
          fave: "#ff3366",        // favorite heart
        },
      },
      fontFamily: {
        display: ['"Archivo"', '"IBM Plex Sans"', "system-ui", "sans-serif"],
        sans: ['"IBM Plex Sans"', "system-ui", "-apple-system", '"Segoe UI"', "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "Consolas", "monospace"],
      },
      fontSize: {
        // [size, line-height]. Body is 13 to 14px, metadata 10 to 11px.
        tag: ["10px", "14px"],
        meta: ["11px", "16px"],
        body: ["13px", "19px"],
        ui: ["14px", "20px"],
        title: ["18px", "22px"],
        head: ["24px", "26px"],
        hero: ["42px", "44px"],
        display: ["76px", "72px"], // the signed-out headline only
      },
      letterSpacing: {
        head: "-0.01em",   // headings tighten a touch; nothing is ever tracked wide
      },
      spacing: {
        // Half-steps for dense rows, plus the named grid gaps.
        "0.5": "2px",
        "1.5": "6px",
        "2.5": "10px",
        "3.5": "14px",
        grid: "14px",
        row: "12px",
        avatar: "32px",
        "avatar-sm": "24px",
        thumb: "36px",
      },
      aspectRatio: {
        cover: "2 / 3",       // modern box art / Steam portrait
        cart: "4 / 3",        // classic horizontal cartridge label
      },
      maxWidth: {
        page: "1280px",
      },
      transitionDuration: {
        fast: "140ms",
      },
    },
  },
};
