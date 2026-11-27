"""The journeys a player actually takes, clicked through in a real browser.

Every test also fails on any page error, console error or failed local request
(see conftest.py), so a broken script anywhere on the way shows up here.
"""
import re

import pytest
from playwright.sync_api import expect

VIEWS = {"library": "library", "feed": "Reviews", "explore": "Explore", "news": "For you",
         "stats": "Playtime", "notifications": "Notifications", "replay": "Year in Review"}


@pytest.mark.parametrize("view,title", VIEWS.items())
def test_every_page_loads_on_its_own(app, view, title):
    page = app.open(f"#view={view}")
    expect(page.locator("#page-title")).to_contain_text(title)


def test_switching_tabs_updates_the_page_and_the_address(app):
    page = app.open()
    for view, title in (("feed", "Reviews"), ("explore", "Explore"), ("library", "library")):
        page.locator(f".topbar [data-view={view}]").click()
        expect(page.locator("#page-title")).to_contain_text(title)
        expect(page).to_have_url(re.compile(f"#view={view}$"))
    expect(page.locator("#welcome")).to_be_hidden()


def test_a_game_opens_and_the_page_behind_it_stays_still(app):
    page = app.open("#view=library")
    page.locator("#games .game-card button").first.click()
    dialog = page.locator("#game-dialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#detail-title")).not_to_be_empty()
    before = page.evaluate("scrollY")
    page.mouse.move(10, 450)
    page.mouse.wheel(0, 2000)
    page.wait_for_timeout(300)
    assert page.evaluate("scrollY") == before
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    page.mouse.wheel(0, 2000)
    page.wait_for_function("before => scrollY > before", arg=before)


def test_liking_a_review_counts_and_survives_a_reload(app):
    page = app.open("#view=feed")
    first = page.locator("#feed-items .post .post-action.like").first
    review = first.get_attribute("data-like-review")
    heart = page.locator(f'#feed-items [data-like-review="{review}"]').first
    expect(heart).to_have_attribute("aria-pressed", "false")
    before = int(re.search(r"(\d[\d,]*) like", heart.get_attribute("aria-label")).group(1).replace(",", ""))
    heart.click()
    expect(heart).to_have_attribute("aria-pressed", "true")
    expect(heart).to_have_attribute("aria-label", re.compile(f"{before + 1:,} like"))
    # The heart paints optimistically, before the demo saves the request.
    page.wait_for_function("id => JSON.parse(sessionStorage.getItem('playgraph-demo-state-v1')).liked.includes(+id)", arg=review)
    page.reload()
    page.wait_for_selector("#page-loading", state="hidden")
    expect(page.locator(f'#feed-items [data-like-review="{review}"]').first).to_have_attribute("aria-pressed", "true")


def test_reposting_opens_the_menu_and_marks_the_post(app):
    page = app.open("#view=feed")
    post = page.locator("#feed-items .post").first
    button = post.locator(".post-action.repost")
    review = button.get_attribute("data-repost-review")
    button.click()
    expect(button).to_have_attribute("aria-expanded", "true")
    post.get_by_role("menuitem", name="Repost").click()
    expect(button).to_have_attribute("aria-pressed", "true")
    page.wait_for_function("id => JSON.parse(sessionStorage.getItem('playgraph-demo-state-v1')).reposted.includes(+id)", arg=review)
    page.reload()
    expect(page.locator(f'#feed-items [data-repost-review="{review}"]').first).to_have_attribute("aria-pressed", "true")


def test_a_play_status_shows_on_the_shelf(app):
    page = app.open("#view=library")
    card = page.locator("#games .game-card").first
    card.locator("button").first.click()
    page.locator("#game-status").select_option("dropped")
    page.keyboard.press("Escape")
    expect(card.locator(".game-meta")).to_contain_text("Dropped")
    page.reload()
    expect(page.locator("#games .game-card").first.locator(".game-meta")).to_contain_text("Dropped")


def test_accessibility_settings_apply_and_stick(app):
    page = app.open()
    page.locator("#accessibility-open").click()
    expect(page.locator("#accessibility-settings")).to_have_attribute("open", "")
    page.locator("#a11y-contrast").check()
    page.locator("#a11y-motion").check()
    page.reload()
    page.wait_for_selector("#page-loading", state="hidden")
    root = page.locator("html")
    expect(root).to_have_attribute("data-high-contrast", "")
    expect(root).to_have_attribute("data-reduced-motion", "")


def test_the_first_tab_press_reaches_skip_to_content(app):
    page = app.open()
    page.keyboard.press("Tab")
    expect(page.locator(":focus")).to_have_text("Skip to content")
    page.keyboard.press("Enter")
    expect(page.locator("#main")).to_be_focused()


def test_a_profile_shows_its_badge_and_crowned_favorite(app):
    page = app.open("#user=1")
    expect(page.locator("#profile-name")).to_contain_text("Jacob")
    expect(page.locator("#profile-name .verified-badge")).to_be_visible()
    expect(page.locator(".favorite.crowned")).to_have_count(1)
    expect(page.locator(".favorite-row .favorite")).to_have_count(4)


@pytest.mark.parametrize("view", ["library", "feed", "explore", "notifications"])
def test_phone_pages_never_scroll_sideways(phone, view):
    page = phone.open(f"#view={view}")
    page.wait_for_timeout(300)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator(".topbar").evaluate("""header => [...header.querySelectorAll('button, a[href], input')]
        .filter(node => node.getClientRects().length)
        .every(node => { const box = node.getBoundingClientRect(); return box.left >= 0 && box.right <= innerWidth; })""")
