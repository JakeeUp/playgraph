"""Untrusted uploads become bounded pixels, and profile privacy applies to reads."""
from io import BytesIO

import pytest
from PIL import Image, PngImagePlugin

from app.config import settings
from app.models import ProfileImage
from app import profile_images
from tests.test_social import ALICE, BOB, api, block, cookie_session, follow, store, who  # noqa: F401
from tests.test_web_app import web  # noqa: F401


def picture(format="PNG", color="red", size=(80, 60)):
    out = BytesIO()
    info = PngImagePlugin.PngInfo(); info.add_text("private", "location metadata")
    Image.new("RGB", size, color).save(out, format=format, **({"pnginfo": info} if format == "PNG" else {}))
    return out.getvalue()


def upload(api, kind="avatar", data=None, user=ALICE):
    return api.put(f"/me/profile-images/{kind}", content=picture() if data is None else data,
                   headers={**who(api, user), "Content-Type": "application/octet-stream"})


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
@pytest.mark.parametrize("kind,size", [("avatar", (256, 256)), ("cover", (1440, 480))])
def test_uploads_are_reencoded_cropped_and_metadata_free(api, db, format, kind, size):
    response = upload(api, kind, picture(format))
    assert response.status_code == 200
    url = response.json()["url"]
    shown = api.get(url)
    assert shown.status_code == 200 and shown.headers["content-type"] == "image/webp"
    assert shown.headers["cache-control"] == "no-store"
    with Image.open(BytesIO(shown.content)) as decoded:
        assert decoded.size == size and decoded.format == "WEBP"
        assert "private" not in decoded.info and not decoded.getexif()
    assert db.query(ProfileImage).count() == 1
    own = api.get("/users/1", headers=who(api, ALICE)).json()
    assert own[f"{kind}_url"] == url


@pytest.mark.parametrize("bad", [b"<svg onload='alert(1)'/>", b"not an image", picture()[:25], picture("GIF")])
def test_invalid_uploads_do_not_replace_existing_image(api, bad):
    original = upload(api).json()["url"]
    assert upload(api, data=bad).status_code == 422
    assert api.get(original).status_code == 200


def test_size_pixel_and_animation_limits(api, monkeypatch):
    assert upload(api, data=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    monkeypatch.setattr(profile_images, "MAX_PIXELS", 100)
    assert upload(api).status_code == 422
    monkeypatch.setattr(profile_images, "MAX_PIXELS", 16000000)
    frames = BytesIO()
    Image.new("RGB", (10, 10), "red").save(frames, format="PNG", save_all=True,
        append_images=[Image.new("RGB", (10, 10), "blue")])
    assert upload(api, data=frames.getvalue()).status_code == 422


def test_replacement_removal_and_author_response(api, db):
    original = upload(api).json()["url"]
    changed = upload(api, data=picture(color="blue")).json()["url"]
    assert changed != original and api.get(original).status_code == 404
    assert db.query(ProfileImage).count() == 1
    assert api.get("/reviews/11").json()["review"]["author_avatar_url"] == changed
    assert api.delete("/me/profile-images/avatar", headers=who(api, ALICE)).status_code == 204
    assert api.get(changed).status_code == 404
    assert api.get("/reviews/11").json()["review"]["author_avatar_url"] is None


def test_images_obey_private_followers_and_blocks(api):
    url = upload(api).json()["url"]
    headers = who(api, ALICE)
    api.patch("/me/profile", headers=headers, json={"profile_visibility": "followers"})
    assert api.get(url).status_code == 404
    assert api.get(url, headers=headers).status_code == 200
    follow(api, BOB, ALICE)
    request = api.get("/me/follow-requests", headers=headers).json()["items"][0]["id"]
    api.post(f"/me/follow-requests/{request}/accept", headers=headers)
    assert api.get(url, headers=who(api, BOB)).status_code == 200
    api.patch("/me/profile", headers=headers, json={"profile_visibility": "private"})
    assert api.get(url, headers=who(api, BOB)).status_code == 404
    api.patch("/me/profile", headers=headers, json={"profile_visibility": "public"})
    block(api, ALICE, BOB)
    assert api.get(url, headers=who(api, BOB)).status_code == 404


def test_upload_ownership_csrf_and_budget(api):
    assert api.put("/me/profile-images/avatar", content=picture()).status_code == 401
    csrf = cookie_session(api, ALICE)
    assert api.put("/me/profile-images/avatar", content=picture()).status_code == 403
    assert api.put("/me/profile-images/avatar", content=picture(), headers={
        "Origin": settings.app_base_url, "X-CSRF-Token": csrf, "Content-Type": "application/octet-stream"}).status_code == 200
    api.cookies.clear()
    for _ in range(11):
        assert upload(api, data=b"bad").status_code == 422
    assert upload(api).status_code == 429
    # Someone else can only update their own image.
    mine = api.get("/users/1", headers=who(api, ALICE)).json()["avatar_url"]
    assert upload(api, user=BOB).json()["url"] != mine
    assert api.get(mine).status_code == 200


def test_only_exact_image_upload_paths_get_the_larger_body_limit(web):
    body = b"x" * 70000
    assert web.put("/me/profile-images/avatar", content=body).status_code == 401
    assert web.put("/me/profile-images/cover", content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    assert web.put("/me/profile-images/avatar", content=iter([b"x" * 1048576, b"x" * 1048576, b"x"])).status_code == 413
    for path in ("/me/profile", "/me/profile-images/other", "/me/profile-images/avatar/extra"):
        assert web.put(path, content=body).status_code == 413
