"""Rebuild nested objects from LinkedIn's normalised Voyager response.

Voyager replies with two parts::

    {
      "data":     { ... mostly URN pointers ... },
      "included": [ { "entityUrn": "urn:li:fsd_profile:ABC", ... }, ... ]
    }

Nothing is nested. ``included`` is a flat pool of entities, and any field whose
name begins with ``*`` holds a URN (or a list of URNs) pointing into that pool.
A profile's experience, for example, is reached by following::

    Profile["*profilePositionGroups"]
        -> CollectionResponse["*elements"]
            -> PositionGroup["*profilePositionInPositionGroup"]
                -> CollectionResponse["*elements"]
                    -> Position

This module walks those pointers and returns ordinary nested dictionaries.
It is pure: no network, no I/O, no globals -- which makes it fully testable
against saved fixtures.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

# Bookkeeping keys that carry no information for consumers of the API.
_NOISE_KEYS = frozenset({"$recipeTypes", "$recipeType"})

# A CollectionResponse is a paging wrapper. We flatten it away and keep only
# the elements it holds.
_COLLECTION_TYPE = "com.linkedin.restli.common.CollectionResponse"

# Bounded so a malformed or cyclic payload cannot spin forever. This counts
# URN *dereferences* -- the only step that can loop -- and deliberately not
# plain structural nesting. Counting nesting would truncate legitimately deep
# data: a company logo sits eleven levels below the profile root, and cutting
# it off silently drops every image from the response.
_MAX_HOPS = 8


def build_index(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map every entity in ``included`` by its ``entityUrn``."""
    index: Dict[str, Dict[str, Any]] = {}
    for entity in payload.get("included") or []:
        urn = entity.get("entityUrn")
        if urn:
            index[urn] = entity
    return index


def _is_collection(entity: Dict[str, Any]) -> bool:
    return entity.get("$type") == _COLLECTION_TYPE


def _elements_of(entity: Dict[str, Any]) -> List[Any]:
    """Return a CollectionResponse's elements, starred or plain.

    LinkedIn uses ``*elements`` when the entries are URN references and a plain
    ``elements`` when the collection is empty, so both spellings must be read.
    """
    if "*elements" in entity:
        return entity["*elements"] or []
    return entity.get("elements") or []


def _resolve(
    node: Any,
    index: Dict[str, Dict[str, Any]],
    hops: int,
    path: Set[str],
) -> Any:
    """Recursively replace URN references with the entities they point to.

    ``hops`` counts URN dereferences only. Walking into a nested dict or list
    is free, because plain structure cannot cycle -- only following a URN can.
    """
    # A bare URN string: look it up, otherwise leave it as-is. Plenty of URNs
    # (companies not requested, geos, industries) are never inlined, and the
    # raw URN is still useful to a caller.
    if isinstance(node, str):
        target = index.get(node)
        # Already on this path, or out of budget: keep the URN rather than
        # recursing. The caller still learns what was referenced.
        if target is None or node in path or hops >= _MAX_HOPS:
            return node
        return _resolve(target, index, hops + 1, path | {node})

    if isinstance(node, list):
        return [_resolve(item, index, hops, path) for item in node]

    if not isinstance(node, dict):
        return node

    # Flatten paging wrappers: a collection becomes the list it contains.
    if _is_collection(node):
        return [
            _resolve(element, index, hops, path)
            for element in _elements_of(node)
        ]

    resolved: Dict[str, Any] = {}
    for key, value in node.items():
        if key in _NOISE_KEYS:
            continue

        # "*profileSkills" resolves and is stored as "profileSkills"; a
        # plain key is copied across untouched.
        if key.startswith("*"):
            resolved[key[1:]] = _resolve(value, index, hops, path)
        else:
            resolved[key] = _resolve(value, index, hops, path)

    return resolved


def find_profile(
    payload: Dict[str, Any],
    public_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the root Profile entity from a Voyager payload.

    A response routinely carries several Profile objects: the member being
    viewed, plus the viewer and anyone referenced in passing. Picking the wrong
    one silently returns the wrong person's data, so the sources of truth are
    tried strongest-first:

    1. ``data["*elements"]`` -- the URNs Voyager itself designates as results.
    2. ``public_id`` -- match the slug that was requested.
    3. The only profile present, if there is exactly one.
    """
    index = build_index(payload)

    def _as_profile(urn: Any) -> Optional[Dict[str, Any]]:
        entity = index.get(urn) if isinstance(urn, str) else None
        if entity and entity.get("$type", "").endswith(
                "identity.profile.Profile"):
            return entity
        return None

    data = payload.get("data") or {}
    for urn in data.get("*elements") or []:
        found = _as_profile(urn)
        if found is not None:
            return found

    root = data.get("*element") or data.get("entityUrn")
    found = _as_profile(root)
    if found is not None:
        return found

    profiles = [
        entity
        for entity in payload.get("included") or []
        if entity.get("$type", "").endswith("identity.profile.Profile")
    ]
    if not profiles:
        return None

    if public_id:
        for profile in profiles:
            if profile.get("publicIdentifier") == public_id:
                return profile

    # Ambiguous: refuse to guess rather than return the wrong member.
    return profiles[0] if len(profiles) == 1 else None


def denormalize(
    payload: Dict[str, Any],
    public_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Turn a normalised Voyager payload into one nested profile dictionary.

    ``public_id`` is the slug that was requested; it disambiguates payloads
    carrying more than one member. Returns ``None`` when no profile can be
    identified, which happens for deleted members and for profiles wholly
    outside the viewer's network.
    """
    profile = find_profile(payload, public_id=public_id)
    if profile is None:
        return None

    index = build_index(payload)
    root_urn = profile.get("entityUrn")
    seed: Set[str] = {root_urn} if root_urn else set()
    return _resolve(profile, index, hops=0, path=seed)


def section(profile: Dict[str, Any], name: str) -> List[Dict[str, Any]]:
    """Read one denormalised section, always as a list.

    Sections arrive as a list, as ``None`` when absent, or occasionally as a
    single object; this smooths over all three so callers can simply iterate.
    """
    value = profile.get(name)
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def iter_positions(profile: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Yield every Position, flattened out of its company grouping.

    LinkedIn groups roles by employer: one PositionGroup per company, each
    holding the individual roles held there. Callers who want a flat job
    history should not have to know that.
    """
    for group in section(profile, "profilePositionGroups"):
        nested = group.get("profilePositionInPositionGroup")
        if isinstance(nested, list):
            for position in nested:
                if isinstance(position, dict):
                    yield position
