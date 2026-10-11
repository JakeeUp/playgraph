"""Build the static demo: the app's real pages and scripts, sample data, and a stand-in API.

    python demo/build.py _site --base /playgraph/

The output is a plain static site for GitHub Pages. --base is the path the
site is served under (the repository name for a project site, "/" locally).
Nothing here runs the server or touches a database; it only needs the
standard library, so CI can build it without installing anything.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
DEMO = Path(__file__).resolve().parent
MARKER = ".playgraph-demo"
sys.path.insert(0, str(DEMO))
import sample_data  # noqa: E402

# Pages that live at the site root, and scripts the demo has no use for
# (real sign-in and PlayStation linking need the server).
PAGES = {"index.html", "account.html", "404.html"}
UNUSED = {"account.js", "psn-account.js"}

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
       "img-src 'self' https://shared.fastly.steamstatic.com https://shared.akamai.steamstatic.com "
       "https://cdn.akamai.steamstatic.com https://cdn.cloudflare.steamstatic.com; "
       "font-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'self'")
BANNER = ('<div class="demo-banner" role="note"><div class="demo-banner-inner">'
          '<strong>Demo</strong><span>Sample data, running in your browser. Anything you post stays in this tab.</span>'
          '<span class="demo-links"><button type="button" id="demo-reset" class="text-button">Reset demo</button>'
          '<a href="https://github.com/JakeeUp/playgraph">Source on GitHub</a></span></div></div>')

# The app is served from the domain root; the demo is not. Absolute links become relative ones.
HTML_RULES = [('="/assets/', '="assets/'), ('href="/app"', 'href="./"'), ('href="/account"', 'href="account.html"')]
CSS_RULES = [('url("/assets/', 'url("')]  # stylesheets sit in assets/, next to fonts/
JS_RULES = [("`${location.origin}/app#", "`${location.href.split('#')[0]}#"), ("`/app#", "`#"),
            ("'/account'", "'account.html'"), ("'/app'", "'./'")]
LEFTOVER = re.compile(r"""["'`(]/(?:app|account|assets)\b|location\.origin\}/app""")


def rewrite(text, rules):
    for old, new in rules:
        text = text.replace(old, new)
    return text


def inject(html, base=None):
    head = '<meta charset="utf-8">'
    assert html.count(head) == 1, "page has no single charset meta to anchor on"
    extra = f'\n  <meta http-equiv="Content-Security-Policy" content="{CSP}">'
    if base:
        extra += f'\n  <base href="{base}">'
    html = html.replace(head, head + extra, 1)
    assert html.count("</head>") == 1
    html = html.replace("</head>", '  <link rel="stylesheet" href="assets/demo.css">\n</head>', 1)
    if "assets/demo-api.js" not in html:
        # Ahead of the app's own script, so fetch is already answered when the app starts.
        scripts = '<script src="assets/demo-data.js"></script>\n  <script src="assets/demo-api.js"></script>\n  '
        anchor = html.find('<script type="module"')
        anchor = anchor if anchor != -1 else html.index("</head>")
        html = html[:anchor] + scripts + html[anchor:]
    skip = re.search(r'<a class="skip-link"[^>]*>[^<]*</a>', html)
    if skip:
        html = html[:skip.end()] + "\n  " + BANNER + html[skip.end():]
    else:
        body = re.search(r"<body[^>]*>", html)
        html = html[:body.end()] + "\n  " + BANNER + html[body.end():]
    return html


def build(out: Path, base: str) -> None:
    out = out.resolve()
    if out in (ROOT, ROOT / "app", STATIC) or ROOT / "app" in out.parents:
        raise SystemExit(f"Refusing to build into {out}")
    if out.exists():
        if any(out.iterdir()) and not (out / MARKER).exists():
            raise SystemExit(f"{out} is not empty and is not a previous demo build")
        shutil.rmtree(out)
    assets = out / "assets"
    assets.mkdir(parents=True)

    for source in STATIC.rglob("*"):
        relative = source.relative_to(STATIC)
        if source.is_dir() or relative.name in PAGES | UNUSED and relative.parent == Path("."):
            continue
        target = assets / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".js":
            target.write_text(rewrite(source.read_text(encoding="utf-8"), JS_RULES), encoding="utf-8")
        elif source.suffix == ".css":
            target.write_text(rewrite(source.read_text(encoding="utf-8"), CSS_RULES), encoding="utf-8")
        else:
            shutil.copyfile(source, target)

    shutil.copyfile(DEMO / "demo-api.js", assets / "demo-api.js")
    shutil.copyfile(DEMO / "demo.css", assets / "demo.css")
    data = json.dumps(sample_data.export(), ensure_ascii=False, separators=(",", ":"))
    (assets / "demo-data.js").write_text(f"window.PLAYGRAPH_DEMO = {data};\n", encoding="utf-8")

    index = rewrite((STATIC / "index.html").read_text(encoding="utf-8"), HTML_RULES)
    (out / "index.html").write_text(inject(index), encoding="utf-8")
    (out / "account.html").write_text(inject((DEMO / "account.html").read_text(encoding="utf-8")), encoding="utf-8")
    # The 404 page is served at any missing path, so it needs a base to find its assets.
    missing = rewrite((STATIC / "404.html").read_text(encoding="utf-8"), HTML_RULES)
    (out / "404.html").write_text(inject(missing, base=base), encoding="utf-8")

    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / MARKER).write_text("Built by demo/build.py\n", encoding="utf-8")

    leftovers = []
    for path in out.rglob("*"):
        if path.suffix in {".html", ".js", ".css"}:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if LEFTOVER.search(line):
                    leftovers.append(f"{path.relative_to(out)}:{number}: {line.strip()[:120]}")
    if leftovers:
        raise SystemExit("Absolute app paths would break under " + base + ":\n" + "\n".join(leftovers))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", type=Path)
    parser.add_argument("--base", default="/", help="path the site is served under, with slashes")
    args = parser.parse_args()
    base = "/" + args.base.strip("/") + "/" if args.base.strip("/") else "/"
    build(args.out, base)
    print(f"Demo built in {args.out} for {base}")


if __name__ == "__main__":
    main()
