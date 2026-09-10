"""Profile URL parsing and credential normalisation."""

from __future__ import annotations

import pytest

from app.linkedin.urls import (
    InvalidProfileURL,
    extract_public_id,
    normalize_li_at,
    profile_url,
)


@pytest.mark.parametrize("value", [
    "https://www.linkedin.com/in/some-member",
    "https://www.linkedin.com/in/some-member/",
    "http://www.linkedin.com/in/some-member",
    "https://linkedin.com/in/some-member",
    "www.linkedin.com/in/some-member",
    "linkedin.com/in/some-member",
    "https://in.linkedin.com/in/some-member",
    "https://uk.linkedin.com/in/some-member/",
    "https://www.linkedin.com/in/some-member?originalSubdomain=in",
    "https://www.linkedin.com/in/some-member/en",
    "  https://www.linkedin.com/in/some-member/  ",
    "some-member",
])
def test_accepts_every_realistic_form(value):
    assert extract_public_id(value) == "some-member"


def test_percent_encoded_identifiers_are_decoded():
    assert extract_public_id(
        "https://www.linkedin.com/in/jos%C3%A9-garcia") == "josé-garcia"


def test_unicode_identifiers_are_accepted():
    assert extract_public_id("https://www.linkedin.com/in/иван") == "иван"


def test_identifiers_with_digits_and_hyphens():
    assert extract_public_id(
        "https://www.linkedin.com/in/some-member-1851a73a5"
    ) == "some-member-1851a73a5"


@pytest.mark.parametrize("value,expected_word", [
    ("https://www.linkedin.com/company/acme", "company"),
    ("https://www.linkedin.com/school/some-university", "school"),
    ("https://www.linkedin.com/posts/someone-activity-123", "post"),
    ("https://www.linkedin.com/jobs/view/123", "job"),
    ("https://www.linkedin.com/groups/456", "group"),
    ("https://www.linkedin.com/pulse/some-article", "article"),
])
def test_non_profile_links_are_named_precisely(value, expected_word):
    """The error should say what the link actually is, not just 'invalid'."""
    with pytest.raises(InvalidProfileURL) as exc:
        extract_public_id(value)
    assert expected_word in str(exc.value)


def test_sales_navigator_is_rejected_with_guidance():
    with pytest.raises(InvalidProfileURL) as exc:
        extract_public_id(
            "https://www.linkedin.com/sales/lead/ACwAAA,NAME,abc")
    assert "Sales Navigator" in str(exc.value)


@pytest.mark.parametrize("value", [
    "", "   ", None,
    "https://example.com/in/some-member",
    "https://twitter.com/someone",
    "https://www.linkedin.com/",
    "https://www.linkedin.com/in/",
])
def test_rejects_unusable_input(value):
    with pytest.raises(InvalidProfileURL):
        extract_public_id(value)


def test_canonical_url_round_trips():
    url = profile_url("some-member")
    assert url == "https://www.linkedin.com/in/some-member"
    assert extract_public_id(url) == "some-member"


# -- credential normalisation -------------------------------------------

@pytest.mark.parametrize("value", [
    "AQEDATest_Token-123456789012345",
    "  AQEDATest_Token-123456789012345  ",
    '"AQEDATest_Token-123456789012345"',
    "li_at=AQEDATest_Token-123456789012345",
    "li_at=AQEDATest_Token-123456789012345;",
    'li_at="AQEDATest_Token-123456789012345"',
])
def test_li_at_is_reduced_to_the_bare_token(value):
    assert normalize_li_at(value) == "AQEDATest_Token-123456789012345"


def test_li_at_is_extracted_from_a_full_cookie_string():
    pasted = (
        'bcookie="v=2&abc"; JSESSIONID="ajax:123"; '
        "li_at=AQEDATest_Token-123456789012345; lidc=\"b=VB25\""
    )
    assert normalize_li_at(pasted) == "AQEDATest_Token-123456789012345"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_credentials_normalise_to_none(value):
    assert normalize_li_at(value) is None
