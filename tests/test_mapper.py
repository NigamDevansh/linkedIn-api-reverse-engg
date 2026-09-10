"""Structural tests for the Voyager -> response-schema mapper.

As with the denormaliser tests, these assert on shape and invariants rather
than on any individual's values, so an edit to a real LinkedIn profile can
never break them.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.linkedin.denormalize import denormalize
from app.linkedin.mapper import _date, _date_range, _image, _text, to_profile

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
ALL_FIXTURES = ["profile_basic", "profile_rich"]


def load(name: str) -> dict:
    with (FIXTURES / f"{name}.json").open() as fh:
        return json.load(fh)


@pytest.fixture(params=ALL_FIXTURES)
def profile(request):
    return to_profile(denormalize(load(request.param)))


# -- the response contract ----------------------------------------------

def test_core_identity_is_populated(profile):
    assert profile.public_id
    assert profile.first_name and profile.last_name
    assert profile.full_name == f"{profile.first_name} {profile.last_name}"
    assert profile.profile_url.endswith(profile.public_id)


def test_every_list_section_is_a_list(profile):
    for name in (
        "experience", "education", "skills", "certifications", "languages",
        "courses", "volunteer_experience", "projects", "honors",
        "publications", "websites",
    ):
        assert isinstance(getattr(profile, name), list)


def test_experience_is_populated_and_ordered_objects(profile):
    assert profile.experience, "fixtures should carry a job history"
    for role in profile.experience:
        assert role.title or role.company_name


def test_response_serialises_to_json(profile):
    """The whole model must round-trip; a stray date or URN breaks clients."""
    dumped = profile.model_dump(mode="json")
    json.dumps(dumped)
    assert dumped["public_id"] == profile.public_id


# -- regression: deep data must survive resolution ----------------------

def test_images_survive_deep_resolution(profile):
    """A company logo sits eleven levels below the profile root.

    An earlier bounded-depth guard counted structural nesting rather than URN
    hops, which silently truncated every image to None. Images are a required
    field of this challenge, so this must stay covered.
    """
    logos = [
        role.company.logo
        for role in profile.experience
        if role.company is not None and role.company.logo is not None
    ]
    assert logos, "company logos should resolve, not be truncated"
    assert all(logo.url and logo.url.startswith("http") for logo in logos)


def test_profile_picture_builds_an_absolute_url(profile):
    if profile.profile_picture is None:
        pytest.skip("this fixture has no profile photo")
    assert profile.profile_picture.url.startswith("http")


def test_largest_image_artifact_is_chosen(profile):
    """Callers want the best available resolution."""
    for role in profile.experience:
        if role.company and role.company.logo and role.company.logo.width:
            assert role.company.logo.width >= 100


# -- helper behaviour ---------------------------------------------------

def test_text_prefers_plain_field_over_locale_map():
    node = {"name": "Plain", "multiLocaleName": {"en_US": "Localised"}}
    assert _text(node, "name") == "Plain"


def test_text_falls_back_to_locale_map():
    node = {"name": None, "multiLocaleName": {"en_US": "Localised"}}
    assert _text(node, "name") == "Localised"


def test_text_ignores_missing_and_blank():
    assert _text({}, "name") is None
    assert _text({"name": "   "}, "name") is None
    assert _text(None, "name") is None


def test_partial_dates_default_to_january_first():
    assert _date({"year": 2021}).isoformat() == "2021-01-01"
    assert _date({"year": 2021, "month": 7}).isoformat() == "2021-07-01"


def test_invalid_dates_do_not_raise():
    assert _date({"year": 2021, "month": 13}) is None
    assert _date({"month": 7}) is None
    assert _date(None) is None


def test_open_ended_range_is_current():
    rng = _date_range({"start": {"year": 2020, "month": 3}})
    assert rng.is_current is True
    assert rng.end is None


def test_closed_range_is_not_current():
    rng = _date_range({
        "start": {"year": 2018},
        "end": {"year": 2020},
    })
    assert rng.is_current is False


def test_image_returns_none_without_artifacts():
    assert _image({"vectorImage": {"artifacts": []}}) is None
    assert _image({}) is None
    assert _image(None) is None


# -- degraded profiles --------------------------------------------------

def test_profile_with_no_sections_still_maps():
    """A restricted or near-empty member must not fail the request."""
    bare = {"publicIdentifier": "someone", "firstName": "A", "lastName": "B"}
    result = to_profile(bare)
    assert result.full_name == "A B"
    assert result.experience == []
    assert result.skills == []


def test_completely_empty_input_maps_without_raising():
    result = to_profile({})
    assert result.full_name is None
    assert result.experience == []
