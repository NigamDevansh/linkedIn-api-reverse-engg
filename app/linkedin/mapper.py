"""Map a denormalised Voyager profile onto the public response schema.

Voyager's shapes are awkward in ways worth naming, because each one is a place
a naive mapper silently loses data:

* Images are split across a ``rootUrl`` and a list of ``artifacts``; the full
  URL is the root concatenated with an artifact's path segment.
* Dates arrive as ``{"year": 2021, "month": 7}`` with no day, and an absent
  ``end`` means "current", not "unknown".
* Text exists twice -- as ``name`` and as ``multiLocaleName`` -- and the two
  can disagree, so the plain field is preferred and the locale map is a
  fallback.
* ``company.industry`` is a dict keyed by URN rather than a list.

Everything here is defensive: any field may be missing on any profile.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from app import models
from app.linkedin.denormalize import iter_positions, section
from app.linkedin.urls import profile_url

_MEDIA_ROOT = "https://media.licdn.com/dms/image/"


# -- primitives ---------------------------------------------------------

def _text(node: Optional[Dict[str, Any]], key: str) -> Optional[str]:
    """Read a text field, falling back to its multi-locale twin."""
    if not isinstance(node, dict):
        return None

    value = node.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()

    locale_key = "multiLocale" + key[0].upper() + key[1:]
    locales = node.get(locale_key)
    if isinstance(locales, dict):
        for lang, text in locales.items():
            if lang.startswith("$"):
                continue
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _date(node: Optional[Dict[str, Any]]) -> Optional[date]:
    """Build a date from Voyager's partial ``{year, month, day}``.

    Missing month or day default to January and the 1st so the value is
    orderable; the original parts are preserved separately on DateRange.
    """
    if not isinstance(node, dict):
        return None
    year = node.get("year")
    if not isinstance(year, int):
        return None
    try:
        return date(year, node.get("month") or 1, node.get("day") or 1)
    except ValueError:
        return None


def _date_range(node: Optional[Dict[str, Any]]) -> Optional[models.DateRange]:
    if not isinstance(node, dict):
        return None
    start, end = node.get("start"), node.get("end")
    return models.DateRange(
        start=_date(start),
        end=_date(end),
        start_year=(start or {}).get("year") if isinstance(start, dict) else None,
        start_month=(start or {}).get("month") if isinstance(start, dict) else None,
        end_year=(end or {}).get("year") if isinstance(end, dict) else None,
        end_month=(end or {}).get("month") if isinstance(end, dict) else None,
        # A range that started but never ended is ongoing.
        is_current=bool(start) and not end,
    )


def _image(node: Optional[Dict[str, Any]]) -> Optional[models.Image]:
    """Pick the largest artifact and build its absolute URL."""
    if not isinstance(node, dict):
        return None

    # Photos nest one level deeper than logos do.
    vector = node.get("vectorImage")
    if vector is None:
        reference = node.get("displayImageReference") or node.get("displayImage")
        if isinstance(reference, dict):
            vector = reference.get("vectorImage")
    if not isinstance(vector, dict):
        return None

    artifacts = [a for a in (vector.get("artifacts") or [])
                 if isinstance(a, dict)]
    if not artifacts:
        return None

    largest = max(artifacts, key=lambda a: (a.get("width") or 0))
    segment = largest.get("fileIdentifyingUrlPathSegment")
    if not segment:
        return None

    root = vector.get("rootUrl") or _MEDIA_ROOT
    return models.Image(
        url=f"{root}{segment}",
        width=largest.get("width"),
        height=largest.get("height"),
    )


def _company(node: Optional[Dict[str, Any]]) -> Optional[models.Company]:
    if not isinstance(node, dict):
        return None

    universal = node.get("universalName")
    industry = None
    # Voyager keys this by URN rather than returning a list.
    raw_industry = node.get("industry")
    if isinstance(raw_industry, dict):
        for value in raw_industry.values():
            if isinstance(value, dict) and value.get("name"):
                industry = value["name"]
                break
        else:
            industry = raw_industry.get("name")

    return models.Company(
        name=_text(node, "name"),
        linkedin_url=(f"https://www.linkedin.com/company/{universal}"
                      if universal else node.get("url")),
        logo=_image(node.get("logo")),
        industry=industry,
        staff_count=node.get("staffCount"),
    )


# -- sections -----------------------------------------------------------

def _experience(profile: Dict[str, Any]) -> List[models.Experience]:
    out: List[models.Experience] = []
    for position in iter_positions(profile):
        out.append(models.Experience(
            title=_text(position, "title"),
            company=_company(position.get("company")),
            company_name=_text(position, "companyName"),
            employment_type=_text(position, "employmentTypeName"),
            location=_text(position, "locationName"),
            description=_text(position, "description"),
            dates=_date_range(position.get("dateRange")),
        ))
    return out


def _education(profile: Dict[str, Any]) -> List[models.Education]:
    out: List[models.Education] = []
    for entry in section(profile, "profileEducations"):
        school = entry.get("school") if isinstance(entry.get("school"), dict) else {}
        universal = school.get("universalName") if school else None
        out.append(models.Education(
            school_name=_text(entry, "schoolName") or _text(school, "name"),
            school_linkedin_url=(
                f"https://www.linkedin.com/school/{universal}"
                if universal else None),
            logo=_image(school.get("logo") if school else None),
            degree=_text(entry, "degreeName"),
            field_of_study=_text(entry, "fieldOfStudy"),
            grade=_text(entry, "grade"),
            activities=_text(entry, "activities"),
            description=_text(entry, "description"),
            dates=_date_range(entry.get("dateRange")),
        ))
    return out


def _certifications(profile: Dict[str, Any]) -> List[models.Certification]:
    return [
        models.Certification(
            name=_text(entry, "name"),
            authority=_text(entry, "authority"),
            license_number=_text(entry, "licenseNumber"),
            url=entry.get("url"),
            dates=_date_range(entry.get("dateRange")),
        )
        for entry in section(profile, "profileCertifications")
    ]


def _volunteer(profile: Dict[str, Any]) -> List[models.VolunteerExperience]:
    return [
        models.VolunteerExperience(
            role=_text(entry, "role"),
            organization=_text(entry, "companyName"),
            cause=_text(entry, "cause"),
            description=_text(entry, "description"),
            dates=_date_range(entry.get("dateRange")),
        )
        for entry in section(profile, "profileVolunteerExperiences")
    ]


def _location(profile: Dict[str, Any]) -> Optional[models.Location]:
    country = (profile.get("location") or {}).get("countryCode")
    name = None
    geo = profile.get("geoLocation")
    if isinstance(geo, dict):
        inner = geo.get("geo")
        if isinstance(inner, dict):
            name = inner.get("defaultLocalizedName")
    if not country and not name:
        return None
    return models.Location(
        country_code=country.upper() if isinstance(country, str) else None,
        name=name,
    )


def _websites(profile: Dict[str, Any]) -> List[str]:
    sites = profile.get("websites")
    if not isinstance(sites, list):
        return []
    return [s["url"] for s in sites
            if isinstance(s, dict) and isinstance(s.get("url"), str)]


# -- entry point --------------------------------------------------------

def to_profile(denormalized: Dict[str, Any]) -> models.Profile:
    """Build the public response object from a denormalised profile."""
    first = _text(denormalized, "firstName")
    last = _text(denormalized, "lastName")
    public_id = denormalized.get("publicIdentifier")

    industry = denormalized.get("industry")
    industry_name = (industry.get("name")
                     if isinstance(industry, dict) else None)

    pronoun = denormalized.get("pronounUnion")
    pronoun_value = (pronoun.get("standardizedPronoun")
                     if isinstance(pronoun, dict) else None)

    return models.Profile(
        public_id=public_id,
        profile_url=profile_url(public_id) if public_id else None,
        first_name=first,
        last_name=last,
        full_name=" ".join(part for part in (first, last) if part) or None,
        headline=_text(denormalized, "headline"),
        about=_text(denormalized, "summary"),
        pronouns=pronoun_value,
        location=_location(denormalized),
        industry=industry_name,
        profile_picture=_image(denormalized.get("profilePicture")),
        background_image=_image(denormalized.get("backgroundPicture")),
        experience=_experience(denormalized),
        education=_education(denormalized),
        skills=[models.Skill(name=_text(s, "name"))
                for s in section(denormalized, "profileSkills")],
        certifications=_certifications(denormalized),
        languages=[models.Language(name=_text(l, "name"),
                                   proficiency=l.get("proficiency"))
                   for l in section(denormalized, "profileLanguages")],
        courses=[models.Course(name=_text(c, "name"), number=c.get("number"))
                 for c in section(denormalized, "profileCourses")],
        volunteer_experience=_volunteer(denormalized),
        projects=[models.Project(
            title=_text(p, "title"),
            description=_text(p, "description"),
            url=p.get("url"),
            dates=_date_range(p.get("dateRange")),
        ) for p in section(denormalized, "profileProjects")],
        honors=[models.Honor(
            title=_text(h, "title"),
            issuer=_text(h, "issuer"),
            description=_text(h, "description"),
            issued_on=_date(h.get("issuedOn")),
        ) for h in section(denormalized, "profileHonors")],
        publications=[models.Publication(
            name=_text(p, "name"),
            publisher=_text(p, "publisher"),
            description=_text(p, "description"),
            url=p.get("url"),
            published_on=_date(p.get("publishedOn")),
        ) for p in section(denormalized, "profilePublications")],
        websites=_websites(denormalized),
        is_premium=bool(denormalized.get("premium")),
        is_influencer=bool(denormalized.get("influencer")),
        is_verified=bool(denormalized.get("showVerificationBadge")),
        is_creator=bool(denormalized.get("creator")),
    )
