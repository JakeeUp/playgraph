// PlayGraph "Arcade Darkroom" theme, Tailwind v3.
// JSON-compatible: no functions or plugins, so the same object can be pasted
// into `tailwind.config = {...}` for the Play CDN or loaded by the CLI.
module.exports = {
  content: ["./**/*.html", "./**/*.js"],
  theme: {
    // Replaced, not extended: there are no shadows anywhere in this system.
    boxShadow: { none: "none" },
    borderRadius: {
      none: "0",
      sm: "2px",
      DEFAULT: "3px",   // covers, inputs, badges
      md: "4px",        // buttons, popovers
      full: "9999px",   // avatars only
    },
    extend: {
      colors: {
        game: {
          bg: "#101418",          // canvas
          surface: "#182026",     // sections, sidebars, list backdrops
          raised: "#222c36",      // popovers, menus, filters
          line: "rgba(255,255,255,0.06)",
          "line-strong": "rgba(255,255,255,0.12)",
          text: "#ffffff",        // headings
          body: "#9bb0c1",        // running text
          muted: "#6e889d",       // metadata; #627d93 from the brief is 4.3:1, this is 4.9:1
          accent: "#ff7a00",      // played, primary action, rating stars
          "accent-hover": "#ff9433",
          playing: "#00b4d8",     // in progress
          done: "#00e054",        // completed / 100%
          fave: "#ff3366",        // favorite heart
        },
      },
      fontFamily: {
        display: ['"Space Grotesk"', '"IBM Plex Sans"', "system-ui", "sans-serif"],
        sans: ['"IBM Plex Sans"', "system-ui", "-apple-system", '"Segoe UI"', "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "Consolas", "monospace"],
      },
      fontSize: {
        // [size, line-height]. Body is 13 to 14px, metadata 10 to 11px.
        tag: ["10px", "14px"],
        meta: ["11px", "16px"],
        body: ["13px", "19px"],
        ui: ["14px", "20px"],
        title: ["17px", "22px"],
        head: ["22px", "26px"],
        hero: ["34px", "38px"],
      },
      letterSpacing: {
        head: "-0.02em",   // headings tighten; nothing is ever tracked wide
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
        fast: "120ms",
      },
    },
  },
};
