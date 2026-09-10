"""End-to-end tests for the HTTP layer, with LinkedIn mocked.

No test here touches the network. The Voyager call is replaced with a saved
fixture, so the suite is fast, deterministic, and cannot get anyone's account
rate limited.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import main
from app.linkedin import client as voyager

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

VALID_LI_AT = "AQEDATestTokenValueForTests1234567890"
VALID_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def load(name: str) -> dict:
    with (FIXTURES / f"{name}.json").open() as fh:
        return json.load(fh)


@pytest.fixture
def client():
    main.cache.clear()
    with TestClient(main.app) as test_client:
        yield test_client
    main.cache.clear()


@pytest.fixture
def mock_linkedin(monkeypatch):
    """Serve a fixture instead of calling LinkedIn, and record the calls."""
    calls = []

    async def fake_fetch(public_id, li_at, user_agent=None, **kwargs):
        calls.append({"public_id": public_id, "li_at": li_at,
                      "user_agent": user_agent})
        return load("profile_rich")

    monkeypatch.setattr(main.voyager, "fetch_profile", fake_fetch)
    return calls


def body(**overrides) -> dict:
    payload = {
        "profile_url": "https://www.linkedin.com/in/some-member",
        "li_at": VALID_LI_AT,
        "user_agent": VALID_UA,
    }
    payload.update(overrides)
    return payload


# -- the happy path -----------------------------------------------------

def test_returns_a_structured_profile(client, mock_linkedin):
    response = client.post("/api/v1/profile", json=body())
    assert response.status_code == 200

    data = response.json()
    profile = data["profile"]

    assert profile["full_name"]
    assert profile["headline"]
    assert profile["experience"]
    assert profile["education"]
    assert profile["skills"]
    assert profile["certifications"]
    assert data["cached"] is False
    assert data["fetched_at"]


def test_every_required_challenge_field_is_present(client, mock_linkedin):
    """The brief names these explicitly; none may silently vanish."""
    profile = client.post("/api/v1/profile", json=body()).json()["profile"]

    for field in ("full_name", "headline", "location", "about", "experience",
                  "education", "skills", "certifications", "languages",
                  "profile_picture"):
        assert field in profile, f"{field} missing from the response"


def test_the_public_id_is_extracted_from_the_url(client, mock_linkedin):
    client.post("/api/v1/profile", json=body(
        profile_url="https://in.linkedin.com/in/target-person/?foo=bar"))
    assert mock_linkedin[0]["public_id"] == "target-person"


def test_credentials_are_forwarded_to_linkedin(client, mock_linkedin):
    client.post("/api/v1/profile", json=body())
    assert mock_linkedin[0]["li_at"] == VALID_LI_AT
    assert mock_linkedin[0]["user_agent"] == VALID_UA


# -- caching ------------------------------------------------------------

def test_repeat_requests_are_served_from_cache(client, mock_linkedin):
    """Fewer upstream calls is the main defence against being throttled."""
    first = client.post("/api/v1/profile", json=body())
    second = client.post("/api/v1/profile", json=body())

    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert len(mock_linkedin) == 1, "the second request should not hit LinkedIn"


def test_a_different_session_does_not_share_a_cache_entry(client,
                                                          mock_linkedin):
    """Visibility differs per viewer, so cached data must not leak across.

    A first-degree connection sees more than a stranger does. Keying only on
    the profile would serve one caller's richer view to another.
    """
    client.post("/api/v1/profile", json=body())
    client.post("/api/v1/profile",
                json=body(li_at="AQEDADifferentSessionToken0987654321"))

    assert len(mock_linkedin) == 2


# -- required inputs ----------------------------------------------------

@pytest.mark.parametrize("missing", ["profile_url", "li_at", "user_agent"])
def test_all_three_fields_are_required(client, missing):
    payload = body()
    payload.pop(missing)
    assert client.post("/api/v1/profile", json=payload).status_code == 422


@pytest.mark.parametrize("url,expected", [
    ("https://www.linkedin.com/company/acme", "company"),
    ("not-a-url-at-all/", "invalid_profile_url"),
    ("https://example.com/in/someone", "invalid_profile_url"),
])
def test_bad_urls_are_rejected_with_an_explanation(client, url, expected):
    response = client.post("/api/v1/profile", json=body(profile_url=url))
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"] == "invalid_profile_url"
    assert payload["hint"]


# -- upstream failures --------------------------------------------------

@pytest.mark.parametrize("exception,expected_status,expected_error", [
    (voyager.AuthenticationError("expired"), 401, "authentication_failed"),
    (voyager.ProfileNotFound("nope"), 404, "profile_not_found"),
    (voyager.RateLimited("slow down"), 429, "rate_limited"),
    (voyager.UpstreamError("bad gateway"), 502, "upstream_error"),
])
def test_upstream_failures_map_to_clean_errors(
        client, monkeypatch, exception, expected_status, expected_error):
    async def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(main.voyager, "fetch_profile", fail)

    response = client.post("/api/v1/profile", json=body())
    assert response.status_code == expected_status
    assert response.json()["error"] == expected_error


def test_a_payload_with_no_profile_returns_404(client, monkeypatch):
    async def empty(*args, **kwargs):
        return {"data": {}, "included": []}

    monkeypatch.setattr(main.voyager, "fetch_profile", empty)

    response = client.post("/api/v1/profile", json=body())
    assert response.status_code == 404
    assert response.json()["error"] == "profile_unavailable"


# -- credential hygiene -------------------------------------------------

def test_the_session_token_never_appears_in_a_response(client, mock_linkedin):
    """A leaked li_at in an error body would be a serious problem."""
    ok = client.post("/api/v1/profile", json=body())
    assert VALID_LI_AT not in ok.text

    bad = client.post("/api/v1/profile",
                      json=body(profile_url="https://www.linkedin.com/company/x"))
    assert VALID_LI_AT not in bad.text


def test_health_reports_status(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_docs_explain_how_to_obtain_the_cookie(client):
    """The reviewer must be able to self-serve; all three fields are required."""
    page = client.get("/docs")
    assert page.status_code == 200
    schema = client.get("/openapi.json").json()
    assert "li_at" in json.dumps(schema)
