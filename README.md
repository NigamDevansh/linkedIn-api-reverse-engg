# LinkedIn Profile API

Takes a LinkedIn profile URL and returns that profile as structured JSON.

It works by calling LinkedIn's internal **Voyager** API directly over HTTP.
There is no browser, no Selenium, no Puppeteer and no headless automation
anywhere in the request path — just an authenticated HTTP request built from
reverse-engineering how linkedin.com talks to its own backend.

```bash
curl -X POST http://localhost:8000/api/v1/profile \
  -H 'content-type: application/json' \
  -d '{
    "profile_url": "https://www.linkedin.com/in/some-member/",
    "li_at": "AQEDAT...",
    "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ..."
  }'
```

```jsonc
{
  "profile": {
    "public_id": "some-member",
    "full_name": "Some Member",
    "headline": "Founder & Director at Example Ltd",
    "location": { "country_code": "CH", "name": "Zürich Metropolitan Area" },
    "about": "…",
    "experience": [ /* every role, not just the most recent */ ],
    "education": [ … ],
    "skills": [ … ],
    "certifications": [ … ],
    "languages": [ … ],
    "profile_picture": { "url": "https://media.licdn.com/…", "width": 800 }
  },
  "fetched_at": "2026-08-30T10:41:02.511Z",
  "cached": false
}
```

---

## Contents

- [Setup](#setup)
- [Getting your `li_at` cookie](#getting-your-li_at-cookie)
- [API documentation](#api-documentation)
- [Approach](#approach)
- [Testing](#testing)
- [Known limitations](#known-limitations)

---

## Setup

Requires Python 3.9 or newer.

```bash
git clone <this-repo>
cd linkedin-profile-api

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API is then at `http://localhost:8000`, with interactive documentation at
**`http://localhost:8000/docs`** — the easiest way to try it, since it renders
a form you can fill in and execute directly.

### Docker

```bash
docker build -t linkedin-profile-api .
docker run -p 8000:8000 linkedin-profile-api
```

### Configuration

Copy `.env.example` to `.env` to adjust cache and timeout settings.

**There are no credentials to configure.** This service holds no LinkedIn
session of its own — every request carries the caller's own cookie, which is
used once and discarded. That is why this repository contains no secrets and
needs none to run.

---

## Getting your `li_at` cookie

Requests run against **your** LinkedIn session, so you must supply it. The
`li_at` cookie is `HttpOnly`, so JavaScript cannot read it and there is no
bookmarklet shortcut — it has to come from DevTools.

**In Chrome or Edge:**

1. Open [linkedin.com](https://www.linkedin.com) and confirm you are signed in
2. Press `F12` (macOS: `Cmd` + `Option` + `I`)
3. Open the **Application** tab
4. In the left sidebar: **Storage → Cookies → `https://www.linkedin.com`**
5. Type `li_at` in the filter box
6. Double-click the **Value** cell and copy it

**In Firefox:** the same, under the **Storage** tab.

For `user_agent`, open `chrome://version` and copy the "User Agent" line — use
the same browser the cookie came from.

> **Use the user agent from the same browser the cookie came from, and turn
> off DevTools device emulation before copying.** LinkedIn treats a device
> change as grounds for invalidating a session, so pairing a cookie captured
> under mobile emulation with a desktop user agent will shorten its life
> considerably. Logging out of LinkedIn also invalidates the cookie
> immediately. See [Known limitations](#known-limitations).

---

## API documentation

### `POST /api/v1/profile`

Fetches a profile and returns it as structured JSON.

**Request body** — all three fields are required:

| Field | Type | Description |
|---|---|---|
| `profile_url` | string | Any LinkedIn profile URL, or a bare profile id |
| `li_at` | string | Your LinkedIn session cookie |
| `user_agent` | string | The user agent of the browser the cookie came from |

`profile_url` accepts every realistic form: with or without a scheme or
`www`, locale subdomains (`in.linkedin.com`), trailing slashes, locale
suffixes (`/in/name/en`), tracking query parameters, percent-encoded and
non-ASCII identifiers, or just `some-member` on its own.

`li_at` is tolerant of how it was pasted — the bare token, a quoted value,
`li_at=AQED…`, or an entire cookie string are all accepted.

**Response `200`**

| Field | Description |
|---|---|
| `profile` | The profile (see schema below) |
| `fetched_at` | When it was retrieved, UTC |
| `cached` | Whether it came from cache rather than LinkedIn |

**Profile schema**

| Field | Type | Notes |
|---|---|---|
| `public_id`, `profile_url` | string | canonical identity |
| `first_name`, `last_name`, `full_name` | string | |
| `headline`, `about`, `pronouns` | string | `about` is the "About" section |
| `location` | object | `country_code`, `name` |
| `industry` | string | |
| `profile_picture`, `background_image` | object | `url`, `width`, `height` — largest available |
| `experience` | array | **all** roles: title, company (with logo and LinkedIn URL), employment type, location, description, dates |
| `education` | array | school, degree, field of study, grade, activities, dates |
| `skills` | array | |
| `certifications` | array | name, authority, licence number, URL, dates |
| `languages` | array | name and proficiency |
| `courses`, `projects`, `honors`, `publications` | array | |
| `volunteer_experience` | array | role, organisation, cause, dates |
| `websites` | array | see limitations |
| `is_premium`, `is_influencer`, `is_verified`, `is_creator` | bool | |

Every field is optional. Profiles vary enormously, so a missing section is
`null` or `[]` — never an error.

Dates use a `DateRange` carrying both a normalised `start`/`end` date and the
raw `start_year` / `start_month` parts, because LinkedIn frequently gives only
a year. `is_current` is true when a range has begun but has no end.

**Errors**

All errors return `{ "error": …, "detail": …, "hint": … }`.

| Status | `error` | Meaning |
|---|---|---|
| `400` | `invalid_profile_url` | Not a member profile. The message names what the link actually is — a company page, a job listing, a post |
| `422` | — | A required field is missing |
| `401` | `authentication_failed` | The `li_at` is expired or was rejected |
| `404` | `profile_not_found` / `profile_unavailable` | No such profile, or not visible to your session |
| `429` | `rate_limited` | LinkedIn is throttling this session |
| `502` | `upstream_error` | LinkedIn was unreachable or replied unusably |

### `GET /health`

```json
{ "status": "ok", "cached_profiles": 3 }
```

---

## Approach

### Finding the endpoint

The obvious starting point, `/voyager/api/identity/profiles/{id}/profileView`,
is what every older tutorial recommends. It returns **`410 Gone`**.

That failure was the most useful signal in the whole exercise. `410` means
"this existed and has been removed" — unlike `404`, it confirms the
`/voyager/api/identity/` namespace is still live. Only the specific resource
had been retired.

Next, LinkedIn's own frontend bundles were fetched from `static.licdn.com` and
searched for API strings. They contained:

```
/voyager/api/voyagerSearchDashSearchHome
/voyager/api/voyagerSocialDashNormComments
com.linkedin.voyager.dash.deco.contentcreation.sharebox-26
```

Two things fall out. `dash` marks LinkedIn's current API generation, and
`com.linkedin.voyager.dash.deco.{domain}.{thing}-{version}` is the shape of a
**decoration id** — the parameter that tells Voyager how much data to inline.

The bundles contained no profile endpoint, so the address was assembled from
the surviving namespace plus the observed conventions, then tested:

```
GET /voyager/api/identity/dash/profiles
      ?q=memberIdentity
      &memberIdentity={public_id}
      &decorationId=com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-101
```

One request returns the complete profile — 123 entities for a modest profile,
163 for a detailed one.

Without the `decorationId` the same endpoint returns only the bare profile and
no sections at all, which is the trap worth knowing about.

### Determining what is actually required

Rather than copying a browser's full header set, each header was removed in
turn to see what genuinely mattered:

| Header | Required | Evidence |
|---|---|---|
| `cookie` | yes | the session |
| `csrf-token` | **yes** | removing it returns `403` |
| `accept: application/vnd.linkedin.normalized+json+2.1` | in practice | without it the response arrives in a different shape with no `included` array |
| browser-like `user-agent` | in practice | a curl-shaped agent is redirected away |
| `x-restli-protocol-version` | no | `200` without it |
| `x-li-lang`, `referer` | no | `200` without them |

Decoration versions `-80`, `-90`, `-100` and `-101` all returned identical
payloads, so the suffix is tolerant. This matters: it means the endpoint is
stable REST rather than a GraphQL persisted query, and there is no rotating
hash to chase. LinkedIn's GraphQL `queryId` hashes rotate every few weeks;
this endpoint does not.

### Only `li_at` is needed

The `csrf-token` header must carry the `JSESSIONID` value, which suggests the
caller must supply both cookies. They do not. LinkedIn issues a `JSESSIONID`
to any valid session, so the client:

1. seeds a session with `li_at` alone,
2. requests `/feed/` once and reads the `JSESSIONID` LinkedIn sets,
3. uses that value as the `csrf-token`,
4. calls the profile endpoint.

Asking for one cookie instead of a whole cookie header is also better for the
person supplying it: a full header would hand over `li_rm`, the long-lived
"remember me" token that can mint fresh sessions, along with a pile of
advertising identifiers. None of that is wanted, so none of it is requested.

### Rebuilding the data

Voyager returns *normalised* JSON: a `data` object of URN pointers plus a flat
`included` array. Nothing is nested. Reaching a job title means walking:

```
Profile["*profilePositionGroups"]
  → CollectionResponse["*elements"]
    → PositionGroup["*profilePositionInPositionGroup"]
      → CollectionResponse["*elements"]
        → Position
```

`app/linkedin/denormalize.py` resolves those references into ordinary nested
objects. It is a pure function — no network, no I/O — so it is tested entirely
against saved fixtures.

Two subtleties are worth naming, because both silently corrupt output:

**Several profiles arrive in one payload.** The subject, the viewer, and
anyone referenced in passing all appear as `Profile` objects. Selecting "the
first profile found" returns the wrong person. The root is taken from
`data["*elements"]`, which is Voyager's own designation of the result, and
ambiguity is refused rather than guessed at.

**Cycle protection must count URN hops, not nesting depth.** An early version
bounded plain structural recursion, which truncated legitimately deep data —
a company logo sits eleven levels below the profile root, so every image
silently became `null`. The guard now counts only URN dereferences, which are
the sole thing that can actually loop.

### Not being throttled

LinkedIn throttles sessions that ask for too much too quickly; during
development it responded with a redirect loop pointing at the requested URL.
Three things address this:

- **Caching.** Repeated profiles are served from memory. In testing, a cached
  response returned in 0.007s against 3.33s for a live fetch.
- **One request per profile.** Sections are not fetched separately.
- **Fixtures.** All parser work runs against saved payloads, so development
  and the test suite make no live calls at all.

The cache key is the profile identifier plus a **hash of the session token**,
never the profile alone. LinkedIn returns more data to a first-degree
connection than to a stranger, so a profile-only key would serve one caller's
richer view to someone not entitled to it.

### Credential handling

`li_at` is held for the duration of a single request and then discarded. It is
never written to disk, never logged, and never included in a response —
there is a test asserting the token cannot appear in any response body,
including error bodies.

Test fixtures are real captured payloads run through
`tools/anonymize_fixture.py`, which replaces identifying values while
preserving structure exactly — same keys, same nesting, same entity counts,
and consistent URN rewriting so references still resolve. It aborts if the
structure changes. No real person's data is committed to this repository.

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

110 tests, no network access, roughly 0.3 seconds.

The tests assert on **structure and invariants**, never on any individual's
values — so nobody editing their LinkedIn profile can break the suite. Among
what is covered: that the member returned is the member requested; that every
URN reference resolves; that images survive deep resolution; that collection
wrappers are flattened; that a session token never leaks into a response; and
that fixtures contain no real LinkedIn identifiers.

---

## Known limitations

**Session tokens expire, and must be refreshed periodically.** A LinkedIn
`li_at` typically remains valid for around two weeks, so callers should expect
to supply a fresh one roughly fortnightly. This matches how comparable tools
operate — PhantomBuster advises its users to refresh on a similar cadence.

Several things end a session early, and all of them are avoidable:

- **Logging out of LinkedIn** invalidates the cookie immediately, as does
  logging in again elsewhere, which issues a new session in its place.
- **A device or IP change** can trigger invalidation. In particular, a cookie
  captured with DevTools device emulation switched on and then used with a
  desktop `user_agent` reads as a device change. Capture with emulation off,
  and send the user agent from the same browser.
- **VPN use** and LinkedIn's own security resets.

During development we saw tokens die within minutes, but that was caused by
the two mistakes above rather than by normal expiry: sessions were repeatedly
rotated by logging out, and a mobile-emulated cookie was paired with a desktop
user agent. Avoiding both gives the expected multi-day lifetime.

**How much data is returned depends on who is asking.** LinkedIn shows more of
a profile to a first-degree connection than to a stranger. The same request
made with two different sessions can legitimately return different amounts of
detail, and a profile far outside your network may return very little.

**`websites` and the verification badge are absent.** These fields exist on
the undecorated endpoint but not under `FullProfileWithEntities-101`, which is
the one carrying experience, education and skills. Retrieving both would mean
two requests per profile, doubling the throttling risk for two non-essential
fields. Single request preferred; documented instead.

**Rate limiting is real.** Sustained use will get a session throttled,
signalled by LinkedIn redirecting requests to themselves. The API reports this
as `429` rather than following the loop. Caching mitigates it; it does not
eliminate it.

**The cache is in-process.** It is not shared between workers and does not
survive a restart. A multi-instance deployment would want Redis.

**Not currently deployed.** The service runs locally and ships with a
`Dockerfile` and `Procfile`, so any container host will run it unchanged.

**Terms of service.** Programmatic access to LinkedIn data conflicts with
LinkedIn's User Agreement, and accounts used this way risk restriction. This
was built as a reverse-engineering exercise; use a throwaway account rather
than one you care about.

---

## Project structure

```
app/
  main.py                 FastAPI routes and error handling
  models.py               Request and response schema
  config.py               Environment-driven settings
  cache.py                TTL cache, keyed per profile + session
  linkedin/
    client.py             Voyager HTTP client and session bootstrap
    denormalize.py        URN graph → nested objects
    mapper.py             Voyager shapes → response schema
    urls.py               Profile URL and credential parsing
tools/
  anonymize_fixture.py    Structure-preserving fixture scrubber
tests/                    110 tests, no network
```
