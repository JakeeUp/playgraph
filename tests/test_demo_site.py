"""The static demo: it builds, works under a subpath, and ships sample data only."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "demo" / "build.py"


def build(out, base="/playgraph/"):
    return subprocess.run([sys.executable, str(BUILD), str(out), "--base", base], capture_output=True, text=True)


def test_demo_builds_with_relative_paths_and_the_stand_in_api_first(tmp_path):
    out = tmp_path / "site"
    assert build(out).returncode == 0
    index = (out / "index.html").read_text(encoding="utf-8")
    # The stand-in API must be in place before the app's module runs.
    assert index.index('src="assets/demo-api.js"') < index.index('src="assets/app.js"')
    assert 'class="demo-banner"' in index and 'http-equiv="Content-Security-Policy"' in index
    assert "unsafe-inline" not in index and "unsafe-eval" not in index
    for page in out.rglob("*"):
        if page.suffix in {".html", ".js", ".css"}:
            text = page.read_text(encoding="utf-8")
            assert '"/assets/' not in text and "'/account'" not in text and "'/app'" not in text, page
    assert (out / ".nojekyll").exists()


def test_demo_ships_sample_data_and_leaves_out_real_sign_in(tmp_path):
    out = tmp_path / "site"
    assert build(out).returncode == 0
    raw = (out / "assets" / "demo-data.js").read_text(encoding="utf-8")
    data = json.loads(raw.removeprefix("window.PLAYGRAPH_DEMO = ").rstrip().rstrip(";"))
    games = {game["id"]: game for game in data["games"]}
    users = {user["id"] for user in data["users"]}
    # Steam games only: no IGDB covers, no PlayStation entries.
    assert all(game["steam_appid"] and not game.get("cover_url") for game in games.values())
    assert all(entry["game_id"] in games for entry in data["library"])
    assert all(review["game_id"] in games and review["user_id"] in users for review in data["reviews"])
    assert {comment["review_id"] for comment in data["comments"]} <= {review["id"] for review in data["reviews"]}
    assert not (out / "assets" / "account.js").exists() and not (out / "assets" / "psn-account.js").exists()
    assert 'id="demo-sign-in"' in (out / "account.html").read_text(encoding="utf-8")
    assert '<base href="/playgraph/">' in (out / "404.html").read_text(encoding="utf-8")


def test_demo_build_only_replaces_its_own_output(tmp_path):
    keep = tmp_path / "notes"
    keep.mkdir()
    (keep / "todo.txt").write_text("mine", encoding="utf-8")
    result = build(keep)
    assert result.returncode != 0 and "not a previous demo build" in result.stderr
    assert (keep / "todo.txt").read_text(encoding="utf-8") == "mine"
    site = tmp_path / "site"
    assert build(site).returncode == 0 and build(site).returncode == 0  # rebuilding its own output is fine
