import re
from pathlib import Path

from tests.test_web_app import web  # noqa: F401


def test_accessibility_controls_and_announcements_are_available_without_an_account(web):  # noqa: F811
    for path in ("/app", "/account"):
        html = web.get(path).text
        for identifier in ("accessibility-open", "accessibility-settings", "a11y-motion", "a11y-contrast", "a11y-shortcuts"):
            assert f'id="{identifier}"' in html
        assert 'aria-controls="accessibility-settings"' in html
        assert 'href="/assets/accessibility.js"' in html
    assert web.get("/assets/accessibility.js").status_code == 200
    html = web.get("/app").text
    assert 'id="error-notice" class="notice error" role="alert"' in html
    assert 'id="news-status" class="sr-only" role="status"' in html
    assert 'id="games" class="game-grid" aria-live=' not in html
    assert 'id="news-items" class="news-items" aria-live=' not in html


def test_control_boundaries_have_three_to_one_contrast_on_both_surfaces():
    css = (Path(__file__).resolve().parents[1] / "app/static/styles.css").read_text(encoding="utf-8")
    def color(name):
        value = re.search(rf"--{name}:#([0-9A-Fa-f]{{6}})", css)[1]
        channels = [int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
        return sum(weight * value for weight, value in zip((.2126, .7152, .0722), linear))
    border = color("rule-strong")
    for name in ("paper", "surface"):
        background = color(name)
        assert (max(border, background) + .05) / (min(border, background) + .05) >= 3
    assert "--text-body:1rem" in css
    assert "html[data-reduced-motion]" in css and "@media (forced-colors:active)" in css
