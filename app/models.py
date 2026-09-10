"""Public response schema.

Every field is optional. LinkedIn profiles vary enormously -- some list a
dozen jobs and no education, some are near-empty, and a member outside the
viewer's network may return little more than a name. A missing section must
never fail the request, so absent data is represented as ``null`` or an empty
list rather than an error.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class DateRange(BaseModel):
    """A start and (optional) end. LinkedIn often gives only a year."""

    start: Optional[date] = None
    end: Optional[date] = None
    start_year: Optional[int] = None
    start_month: Optional[int] = None
    end_year: Optional[int] = None
    end_month: Optional[int] = None
    is_current: bool = False


class Image(BaseModel):
    """A profile photo or banner, at the largest size LinkedIn offers."""

    url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


class Company(BaseModel):
    name: Optional[str] = None
    linkedin_url: Optional[str] = None
    logo: Optional[Image] = None
    industry: Optional[str] = None
    staff_count: Optional[int] = None


class Experience(BaseModel):
    title: Optional[str] = None
    company: Optional[Company] = None
    company_name: Optional[str] = None
    employment_type: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    dates: Optional[DateRange] = None


class Education(BaseModel):
    school_name: Optional[str] = None
    school_linkedin_url: Optional[str] = None
    logo: Optional[Image] = None
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    grade: Optional[str] = None
    activities: Optional[str] = None
    description: Optional[str] = None
    dates: Optional[DateRange] = None


class Certification(BaseModel):
    name: Optional[str] = None
    authority: Optional[str] = None
    license_number: Optional[str] = None
    url: Optional[str] = None
    dates: Optional[DateRange] = None


class Language(BaseModel):
    name: Optional[str] = None
    proficiency: Optional[str] = None


class Skill(BaseModel):
    name: Optional[str] = None


class Course(BaseModel):
    name: Optional[str] = None
    number: Optional[str] = None


class VolunteerExperience(BaseModel):
    role: Optional[str] = None
    organization: Optional[str] = None
    cause: Optional[str] = None
    description: Optional[str] = None
    dates: Optional[DateRange] = None


class Project(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    dates: Optional[DateRange] = None


class Honor(BaseModel):
    title: Optional[str] = None
    issuer: Optional[str] = None
    description: Optional[str] = None
    issued_on: Optional[date] = None


class Publication(BaseModel):
    name: Optional[str] = None
    publisher: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    published_on: Optional[date] = None


class Location(BaseModel):
    country_code: Optional[str] = None
    name: Optional[str] = None


class Profile(BaseModel):
    """A LinkedIn member profile as structured JSON."""

    public_id: Optional[str] = None
    profile_url: Optional[str] = None

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    headline: Optional[str] = None
    about: Optional[str] = None
    pronouns: Optional[str] = None

    location: Optional[Location] = None
    industry: Optional[str] = None

    profile_picture: Optional[Image] = None
    background_image: Optional[Image] = None

    experience: List[Experience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    skills: List[Skill] = Field(default_factory=list)
    certifications: List[Certification] = Field(default_factory=list)
    languages: List[Language] = Field(default_factory=list)
    courses: List[Course] = Field(default_factory=list)
    volunteer_experience: List[VolunteerExperience] = Field(
        default_factory=list)
    projects: List[Project] = Field(default_factory=list)
    honors: List[Honor] = Field(default_factory=list)
    publications: List[Publication] = Field(default_factory=list)

    websites: List[str] = Field(default_factory=list)

    is_premium: bool = False
    is_influencer: bool = False
    is_verified: bool = False
    is_creator: bool = False


class ProfileRequest(BaseModel):
    """Input. All three fields are required.

    The session belongs to the caller: their ``li_at`` and the user agent it
    was captured alongside. Requests are made on their behalf and against
    their own rate limit, and the token is never stored or logged.
    """

    profile_url: str = Field(
        ...,
        description="LinkedIn profile URL, e.g. "
                    "https://www.linkedin.com/in/some-member",
        examples=["https://www.linkedin.com/in/some-member"],
    )
    li_at: str = Field(
        ...,
        min_length=20,
        description="Your LinkedIn li_at session cookie. "
                    "DevTools > Application > Cookies > linkedin.com > li_at",
    )
    user_agent: str = Field(
        ...,
        min_length=20,
        description="The user agent of the browser the cookie came from.",
        examples=[
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ],
    )


class ProfileResponse(BaseModel):
    profile: Profile
    fetched_at: datetime
    cached: bool = False


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    hint: Optional[str] = None
