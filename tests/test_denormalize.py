"""Structural tests for the Voyager denormaliser.

These assert on *shape*, never on any individual's values. The fixtures are
anonymised captures (see ``tools/anonymize_fixture.py``), so no real person's
details are committed, and nobody editing their LinkedIn profile can break the
suite. What is tested is the contract the parser must hold for any profile:
references resolve, sections are lists, and the member returned is the member
that was asked for.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.linkedin.denormalize import (
    build_index,
    denormalize,
    find_profile,
    iter_positions,
    section,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
ALL_FIXTURES = ["profile_basic", "profile_rich"]


def load(name: str) -> dict:
    with (FIXTURES / f"{name}.json").open() as fh:
        return json.load(fh)


@pytest.fixture(params=ALL_FIXTURES)
def payload(request) -> dict:
    return load(request.param)


@pytest.fixture
def profile(payload) -> dict:
    return denormalize(payload)


# -- identity -----------------------------------------------------------

def test_returns_the_member_voyager_designated(payload):
    """The root must be the profile ``data["*elements"]`` names.

    A payload routinely carries several members -- the subject, the viewer,
    and anyone referenced in passing -- so picking by "first profile found"
    silently returns the wrong person.
    """
    designated = (payload.get("data") or {}).get("*elements") or []
    assert designated, "fixture should designate a root element"

    index = build_index(payload)
    expected = index[designated[0]]

    assert find_profile(payload)["entityUrn"] == expected["entityUrn"]


def test_ambiguous_payload_is_refused_rather_than_guessed():
    """With no designation and several profiles, return None, not a guess."""
    payload = load("profile_rich")
    payload["data"] = {}

    profiles = [
        e for e in payload["included"]
        if e.get("$type", "").endswith("identity.profile.Profile")
    ]
    assert len(profiles) > 1, "fixture must hold multiple members"
    assert find_profile(payload) is None


def test_public_id_disambiguates_when_designation_is_missing():
    payload = load("profile_rich")
    payload["data"] = {}
    wanted = next(
        e["publicIdentifier"] for e in payload["included"]
        if e.get("$type", "").endswith("identity.profile.Profile")
        and e.get("publicIdentifier")
    )
    assert find_profile(payload, public_id=wanted)["publicIdentifier"] == wanted


# -- reference resolution -----------------------------------------------

def test_no_unresolved_reference_keys_remain(profile):
    """Every ``*key`` must be rewritten to a plain key."""

    def starred(node, depth=0):
        if depth > 10:
            return []
        if isinstance(node, dict):
            found = [k for k in node if k.startswith("*")]
            for value in node.values():
                found += starred(value, depth + 1)
            return found
        if isinstance(node, list):
            return [k for item in node for k in starred(item, depth + 1)]
        return []

    assert starred(profile) == []


def test_company_reference_resolves_to_an_object(profile):
    groups = section(profile, "profilePositionGroups")
    assert groups, "every fixture should have at least one employer"

    resolved = [g["company"] for g in groups if isinstance(g.get("company"), dict)]
    assert resolved, "company URNs should resolve into objects, not stay strings"
    assert any("name" in company for company in resolved)


def test_collection_wrappers_are_flattened(profile):
    """Sections arrive as lists, never as CollectionResponse wrappers."""
    for name in ("profileSkills", "profileEducations", "profilePositionGroups"):
        value = profile.get(name)
        if value is None:
            continue
        assert isinstance(value, list), f"{name} should be a list"
        assert not any(
            isinstance(item, dict) and "paging" in item for item in value
        ), f"{name} still contains a paging wrapper"


# -- nesting ------------------------------------------------------------

def test_positions_nest_inside_employer_groups(profile):
    """Experience is two levels: an employer, holding the roles held there."""
    groups = section(profile, "profilePositionGroups")
    nested = [
        g for g in groups
        if isinstance(g.get("profilePositionInPositionGroup"), list)
    ]
    assert nested, "at least one employer should hold nested roles"


def test_iter_positions_flattens_every_role(profile):
    """The flat job history must cover all roles across all employers."""
    groups = section(profile, "profilePositionGroups")
    expected = sum(
        len(g.get("profilePositionInPositionGroup") or [])
        for g in groups
        if isinstance(g.get("profilePositionInPositionGroup"), list)
    )
    positions = list(iter_positions(profile))

    assert len(positions) == expected
    assert all(isinstance(p, dict) for p in positions)


def test_nesting_depth_is_bounded(profile):
    """Resolution must terminate; a cyclic payload must not recurse forever."""

    def depth(node, level=0):
        if level > 40:
            raise AssertionError("resolution exceeded a sane depth")
        if isinstance(node, dict):
            return max([level] + [depth(v, level + 1) for v in node.values()])
        if isinstance(node, list):
            return max([level] + [depth(v, level + 1) for v in node])
        return level

    assert depth(profile) <= 40


# -- tolerance ----------------------------------------------------------

def test_missing_section_reads_as_empty_list(profile):
    assert section(profile, "profileNonexistentSection") == []


def test_sections_are_always_lists(profile):
    for name in (
        "profileSkills",
        "profileEducations",
        "profileCertifications",
        "profileLanguages",
        "profileCourses",
        "profileVolunteerExperiences",
    ):
        assert isinstance(section(profile, name), list)


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {"data": {}, "included": []},
        {"included": [{"$type": "com.linkedin.other.Thing"}]},
    ],
)
def test_payload_without_a_profile_returns_none(broken):
    assert denormalize(broken) is None


def test_entities_without_urns_do_not_break_indexing():
    payload = load("profile_basic")
    payload["included"].append({"$type": "com.linkedin.common.Thing"})
    assert denormalize(payload) is not None


# -- privacy ------------------------------------------------------------

def test_fixtures_contain_only_synthetic_identifiers(payload):
    """Guard against a real capture being committed by mistake."""
    raw = json.dumps(payload)
    import re

    for profile_id in set(re.findall(r"ACoAA[A-Za-z0-9_-]+", raw)):
        assert profile_id[5:].isdigit(), (
            f"{profile_id} looks like a real LinkedIn profile id; "
            "run tools/anonymize_fixture.py before committing"
        )
