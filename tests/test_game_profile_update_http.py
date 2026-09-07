"""
Regression test: GameProfile PUT must send application/json, not text/plain.

Blocker 1 (422) came from the browser PUT being sent with
Content-Type: text/plain;charset=UTF-8 because the body was a
pre-stringified JSON string.  FastAPI cannot parse a string body into a
`dict` parameter when the Content-Type is not application/json, so it
returns 422.

The fix (committed in 510cf1a) changed apiJson to serialize object
bodies with JSON.stringify and set Content-Type: application/json,
and changed the modal to pass the body as an object rather than a
string.

This test exercises the REAL endpoint through ASGITransport in local
mode (LOCAL_GAMEPROFILES=1) to prove both:
  1. JSON PUT  →  200  (the fixed path)
  2. text/plain PUT  →  422  (the broken path, confirming root cause)
"""
import os
import sys
import json
import shutil
import tempfile
import asyncio
import uuid

# ── Set LOCAL_GAMEPROFILES BEFORE importing app ──────────────────────
# conftest.py already sets BILLING_ENABLED=0 and adds repo root to
# sys.path.  LOCAL_GAMEPROFILES is read per-request by app.py, so it
# can be set here (or in a fixture) without re-importing.
os.environ["LOCAL_GAMEPROFILES"] = "1"

# Import app (conftest ensures repo root is on sys.path)
import httpx
import pytest
from app import app


# ── Helpers ──────────────────────────────────────────────────────────

def _put(profile_id: str, body: bytes, content_type: str) -> httpx.Response:
    """Issue a PUT via ASGITransport and return the Response (sync wrapper)."""
    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.put(
                f"/api/game-profiles/{profile_id}",
                content=body,
                headers={"Content-Type": content_type},
            )
    return asyncio.run(_run())


def _seed_profile(name: str = "Regression Test") -> str:
    """Create a profile in the local repo and return its ID."""
    from cloud.local_game_profiles import LocalGameProfileRepository
    repo = LocalGameProfileRepository()
    return repo.create_profile({"name": name, "game_title": "Test Game"})


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def tmp_profiles_dir(tmp_path, monkeypatch):
    """Redirect LOCAL_PROFILES_PATH to a temp dir so we don't touch real data."""
    import cloud.local_game_profiles as lgp

    # Patch the module-level constants BEFORE any repository call
    monkeypatch.setattr(lgp, "LOCAL_PROFILES_DIR", str(tmp_path))
    monkeypatch.setattr(lgp, "LOCAL_PROFILES_PATH", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    yield tmp_path


# ── Tests ────────────────────────────────────────────────────────────

class TestGameProfileUpdateHttp:
    """
    End-to-end tests of PUT /api/game-profiles/{id} in local mode.

    These prove the 422 root cause (Content-Type) and the fix
    (application/json) at the HTTP layer, without a live server.
    """

    def test_json_put_returns_200(self, tmp_profiles_dir):
        """
        The fixed path: body is a JSON object with
        Content-Type: application/json.  Must return 200.
        """
        profile_id = _seed_profile("JSON PUT Test")
        body = json.dumps({
            "name": "JSON PUT Updated",
            "game_title": "Test Game",
            "active_weights": {"emotional_reaction": 0.5, "surprise": 0.3},
        }).encode()

        resp = _put(profile_id, body, "application/json")

        assert resp.status_code == 200, (
            f"Expected 200 for application/json PUT, "
            f"got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data["name"] == "JSON PUT Updated"
        assert data["game_title"] == "Test Game"
        assert data["active_weights"]["surprise"] == 0.3

    def test_text_plain_put_returns_422(self, tmp_profiles_dir):
        """
        The broken path: body is a raw JSON string with
        Content-Type: text/plain;charset=UTF-8.
        FastAPI rejects it because it cannot coerce a text/plain
        body into a dict parameter.
        This confirms the 422 is caused by the Content-Type,
        not by a bug in the handler itself.
        """
        profile_id = _seed_profile("TextPlain Test")
        body = b'{"name":"TextPlain Updated","game_title":"Test Game"}'
        resp = _put(profile_id, body, "text/plain;charset=UTF-8")

        # FastAPI rejects non-JSON content types for dict body params.
        # The exact status depends on the FastAPI version:
        #   - 422 if it tries to parse and fails
        #   - 415 (Unsupported Media Type) if it checks the header first
        # Both confirm the root cause is the Content-Type.
        assert resp.status_code in (415, 422), (
            f"Expected 415 or 422 for text/plain PUT, "
            f"got {resp.status_code}: {resp.text}"
        )

    def test_json_put_preserves_all_fields(self, tmp_profiles_dir):
        """
        The PUT handler in local mode replaces the profile record.
        This test documents that the caller must send the full
        object (the modal does this via the formData state).
        """
        profile_id = _seed_profile("Full Object Test")
        body = json.dumps({
            "name": "Full Object Updated",
            "game_title": "Test Game",
            "steam_app_id": "99999",
            "steam_genres": ["Action", "Horror"],
            "active_weights": {"surprise": 0.9, "fear": 0.7},
        }).encode()

        resp = _put(profile_id, body, "application/json")
        assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["name"] == "Full Object Updated"
        assert data["steam_app_id"] == "99999"
        assert data["active_weights"]["surprise"] == 0.9
        assert data["active_weights"]["fear"] == 0.7

    def test_missing_name_returns_400(self, tmp_profiles_dir):
        """The handler requires 'name' in the body."""
        profile_id = _seed_profile("Missing Name Test")
        body = json.dumps({"game_title": "Test Game"}).encode()
        resp = _put(profile_id, body, "application/json")
        assert resp.status_code == 400, f"got {resp.status_code}: {resp.text}"

    def test_missing_game_title_returns_400(self, tmp_profiles_dir):
        """The handler requires 'game_title' in the body."""
        profile_id = _seed_profile("Missing Title Test")
        body = json.dumps({"name": "Some Name"}).encode()
        resp = _put(profile_id, body, "application/json")
        assert resp.status_code == 400, f"got {resp.status_code}: {resp.text}"
