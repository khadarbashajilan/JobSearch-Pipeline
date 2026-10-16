---
name: job-search-profile
description: >
  Maintain the machine-readable job-search profile block in resume.md so ./run.sh
  scrapes correctly targeted listings. Trigger when the user edits their resume
  (adds/removes a target title, skill, keyword, or location), changes seniority
  level, says "update my job search profile", "regenerate my profile", "refresh
  my job targets", asks how to narrow or widen seniority ("only junior roles",
  "include staff/principal", "no level ceiling", "level filter"), or reports a
  profile validation error from ./run.sh. Rewrites ONLY the toml block under
  "## Job Search Profile" in resume.md. Never edits the rest of the resume and
  never invents skills the resume does not evidence.
---

## What this does

`./run.sh` scrapes job boards using a profile that lives in `resume.md`. This skill
keeps that profile honest. It replaces a previously-used local 4B model that was
asked to infer the profile from the resume on every run — that model mangled boolean
syntax (it emitted chained ANDs, which returns nothing). Extraction is now explicit
and editable, so no model is involved at runtime.

## The contract

Inside `resume.md`, under the `## Job Search Profile` header, there is exactly one
fenced ```` ```toml ```` block. That block is the only thing `./run.sh` reads.

```toml
[titles]
primary = "Full-Stack Developer"
also    = ["Full Stack Engineer", "Software Engineer", "Frontend Developer", "Backend Developer", "React Developer", "Python Developer"]

[search]
location         = "Bengaluru, India"
country          = "India"
hours_old        = 72
results_per_site = 30
remote           = "any"             # onsite | remote | hybrid | any
employment_types = ["full_time"]     # full_time, part_time, contract, temporary, internship
# Set true to *also* pass hours_old to Indeed's own server-side date filter.
# Off by default because it halves results in a thin market.
indeed_date_filter = false

[keywords]
core    = ["Python", "FastAPI", "React", "TypeScript", "PostgreSQL", "Docker"]
# Matched against job titles by scrape.py, not sent to any board. Deliberate:
# LinkedIn ignores -X outright, and on Indeed each exclusion sharply narrows
# the pool (5 excludes took 8 rows down to 1 at 72h).
exclude = ["Senior", "Lead", "Principal", "Staff", "Manager"]
# Hard seniority ceiling. `junior` drops mid/senior/lead/staff/principal/director/head/VP
# titles before matching. Switch to "off" to widen.
# Valid: off | intern | junior | mid | senior | lead | staff | principal
level_filter = "junior"

[experience]
years = 1   # optional; used to estimate seniority fit

[matching]
min_score = 0.0    # jobs below this score are dropped from the report

[queries]
# Override the boolean query sent to Indeed. Omit to derive from titles + core.
# The default derived form DOES include -<exclude…> terms, which is why 5
# excludes can take 8 rows to 1 — see "Hard-won site constraints" below.
indeed = 'Full-Stack (Python OR React OR TypeScript OR FastAPI OR PostgreSQL)'
```

### Field semantics

| Field | Required | Meaning |
|---|---|---|
| `titles.primary` | yes | Single best-fit title. Becomes the quoted phrase in the Indeed query and the leading word everywhere else. Pick the title that best matches the bulk of the resume, not the fanciest one. |
| `titles.also` | no | Alternative titles. Folded into the plain query so they actually participate in discovery. Keep it short and ordered by relevance. |
| `search.location` | yes | Free text, e.g. `"Bengaluru, India"`. Commas are safe — that is the whole reason this is TOML and not comma-split prose. |
| `search.country` | no | JobSpy country. Must match a `Country` value in `jobspy/model.py` (e.g. `India`, `USA`, `UK`). Defaults to `USA`. Omit for sites that don't need it. |
| `search.hours_old` | no | Keep only jobs posted within N hours. `72` ≈ 3 days. Applied **locally** to `date_posted` for every site, so behaviour is identical across boards. |
| `search.results_per_site` | no | Target count per site. Default 30. Boards cap out near 1000 per search. Raise if post-filtering empties a site — over-fetching is cheap when you filter locally. |
| `search.remote` | no | `onsite`, `remote`, `hybrid`, or `any`. Used for hard filtering and ranking. Default `any`. |
| `search.employment_types` | no | Acceptable employment types. Default `["full_time"]`. Used for hard filtering. |
| `search.indeed_date_filter` | no | Default `false`. When `true`, *also* passes `hours_old` to Indeed's own server-side filter. Off by default because it halves results in a thin market. |
| `keywords.core` | yes (min 2) | **Ordered by significance.** First 6 feed the plain query. Put the strongest signal first. |
| `keywords.exclude` | no | Dropped from job **titles** client-side. Applies to every site, LinkedIn included. Whole-word match — `"Java"` will not match `"JavaScript"`. |
| `keywords.level_filter` | no | Seniority ceiling. `off` (default) keeps every job. `intern` / `junior` / `mid` / `senior` / `lead` / `staff` / `principal` drop jobs whose inferred level ranks above the setting. Director / Head / VP / Chief titles are treated as senior-tier. Unstated seniority passes through. |
| `experience.years` | no | Years of relevant experience. Used to estimate seniority fit. Optional. |
| `matching.min_score` | no | Jobs scoring below this (0.0–1.0) are hidden from the report. Default 0.0. |
| `llm.provider` / `llm.model` | no | Reserved. Currently unused; matching is fully deterministic. Set only when the LLM integration is enabled. |
| `queries.indeed` | no | Overrides the query sent to Indeed. Omit to derive it. See below. |

## How the queries are derived

`jobsearch/queries.py` builds one query per site capability, then filters the
combined results locally by date and by title.

**Indeed** — `queries.indeed` if set, otherwise derived as
`"<titles.primary>" (<core…>) -<exclude…>`.
**Every other site** — `titles.primary` + every `keywords.core` entry + every
`titles.also` entry. Alternative titles do extend the pool.

`keywords.exclude` is sent to Indeed as `-X` terms by the default derived query
(see `jobsearch/queries.py`), and that is the single reason a 5-exclude set takes
8 rows to 1. It is **also** applied locally to job titles on every site, so
LinkedIn (which ignores `-X` outright) still benefits from the title filter.
`queries.indeed` is the user's escape hatch: setting it overrides the derived
form, and the project's convention is to leave excludes *out* of the override
string to keep the pool wider.

Do not put boolean operators into `keywords.core` or `titles.*`. Values are keywords
and titles, nothing else. `queries.indeed` is the only place boolean syntax lives.

## Hard-won site constraints

Measured by probing the live boards, not inferred. All figures are the raw GraphQL
page for Bengaluru, radius 50, `hours_old = 72`.

- **Indeed's `-X` exclusions work, but over-narrow.** With the same title and window:

  | query | rows | senior |
  |---|---|---|
  | `GenAI Developer` | 9 | 5 |
  | + 3 `OR` terms | 8 | 4 |
  | + 6 `OR` terms | 8 | 4 |
  | + 6 `OR` terms + 5 excludes | **1** | **0** |

  The exclusions do exactly what they should — zero senior rows. The cost is pool
  size: five of them turned 8 rows into 1. That is why `queries.indeed` deliberately
  carries no excludes and titles are filtered locally instead. Widen `queries.indeed`
  or raise `search.hours_old` if Indeed comes back thin.
- **Indeed's `OR` group barely matters.** 0 / 1 / 3 / 6 terms gave 9 / 8 / 8 / 8 rows.
  Do not spend profile effort lengthening it.
- **LinkedIn ignores `-X` exclusions** and still returns "Senior AI Engineer" for a
  query containing `-Senior`. Verified repeatedly. Excludes are filtered locally.
- **Indeed's server-side `hours_old` filter works correctly.** Raw API: `72h → 9`,
  `168h → 36`, `720h → 100` — monotonic, as it should be. It is off by default purely
  to keep one local rule across all sites. `search.indeed_date_filter = true` enables it.
  (An earlier claim that this filter was broken was wrong: it came from throttled
  responses and from comparing runs with different `results_wanted`.)
- **Indeed only accepts one of** `hours_old`, (`job_type` + `is_remote`), `easy_apply`.
- **Google ignores most parameters** if `google_search_term` is set, so the runtime
  passes `search_term` + `location` + `hours_old` and lets the scraper build the query.
- **Indeed is intermittently flaky** — identical queries returned 5 rows one minute
  and 0 the next. `jobsearch/scraper.py` retries a site group once and always reports
  per-site counts, including zeros, so a blank site is visible rather than silent. An
  empty Indeed section usually means throttling, not a bad profile.
- **Blocked from many IPs, not broken:** Naukri returns `406 recaptcha required`,
  ZipRecruiter and Bayt return `403`, Glassdoor returns `400`, BDJobs returns nothing.
  Defaults are `indeed,linkedin`. If a user wants another site, expect it may return
  zero rows — check the error log before assuming a profile bug.
- **Greenhouse, Lever, Ashby** are direct JSON feeds published by each ATS for
  embedding on company sites. They are not scraped boards; the integration is a single
  GET against the public URL. They are off by default — set
  `JOBSEARCH_GREENHOUSE_BOARD=<token>` (or `LEVER`, `ASHBY`) to enable.

## When to run this skill

Trigger on: resume content changed, a title/skill/location was added or removed, the
user asks what `run.sh` will search for, the user reports `run.sh` failing with a
profile validation error, **or the user asks to narrow by seniority** (e.g. *"only
show me junior roles"*, *"filter to entry-level"*, *"I want senior+ only"*, *"stop
showing me senior jobs"*, *"level filter"*, *"targeting senior only"*). For seniority
requests, change `keywords.level_filter` to the matching tier (default is `off`):

| User intent | Setting |
|---|---|
| *"junior / entry-level only"*, *"only entry-level"*, *"filter to junior"*, *"I want my first AI job"* | `level_filter = "junior"` |
| *"mid-level roles"*, *"2–5 years experience"* | `level_filter = "mid"` |
| *"senior roles"*, *"senior+"* | `level_filter = "senior"` |
| *"include staff / principal"*, *"no level ceiling"* | `level_filter = "off"` |

Confirm by running `./run.sh --dry-run` and showing the user the new
`level filter` line.

| User intent | Setting |
|---|---|
| *"intern / first job"*, *"internship only"* | `level_filter = "intern"` |
| *"junior / entry-level only"*, *"only entry-level"*, *"filter to junior"*, *"I want my first AI job"* | `level_filter = "junior"` |
| *"mid-level roles"*, *"2–5 years experience"* | `level_filter = "mid"` |
| *"senior roles"*, *"senior+"* | `level_filter = "senior"` |
| *"lead / staff only"*, *"lead roles"* | `level_filter = "lead"` |
| *"include staff / principal"*, *"no level ceiling"*, *"open to anything"*, *"stop filtering by level"* | `level_filter = "off"` |

## How to update the block

1. Read `resume.md` in full. The profile must reflect the resume above it.
2. Derive from what is actually written. **Never add a skill the resume does not
   evidence.** If the user's actual target differs from what the resume shows, ask —
   do not quietly profile them for a job they do not want.
3. Preserve TOML typing: strings in double quotes, arrays inline, `true`/`false`
   bare. Comments are legal and welcome.
4. Replace only the block between the ```` ```toml ```` fences. Leave the header and
   the prose above it intact. Never reformat the experience or education sections.
5. Keep `keywords.core` at ~6 entries in descending significance. Extra entries are
   silently dropped past the sixth, so a long list gives a false impression of coverage.
   Note this list feeds the plain query only — if `queries.indeed` is set, that key
   governs the Indeed query and `core` no longer reaches Indeed.
6. For seniority, put senior-only noise in `keywords.exclude`
   (`Senior`, `Lead`, `Principal`, `Staff`, `Manager`) rather than dropping roles, so
   the target title stays a single clean phrase. These are matched against job titles
   locally, so they cost result *rows*, not query quality — but see the Indeed table
   above, where five excludes took 8 rows to 1. For a hard ceiling (e.g. only junior
   and entry-level roles reach the report), use `keywords.level_filter = "junior"`
   instead — that drops mid/senior/lead/staff/principal/director/head/VP titles
   before matching. Switch back to `level_filter = "off"` when targeting more senior
   roles.
7. `queries.indeed` is the knob that widens the pool when Indeed looks thin. Widen the
   `OR` group or drop the quoted phrase there. Do not reach for `keywords.core` instead.
8. Report back: what changed, and the exact queries it produces per site.

## Verifying a change

```bash
./run.sh --dry-run     # prints the derived queries, scrapes nothing
```

`--dry-run` is cheap and needs no job board traffic. Use it after every edit.

For full diagnostics, run `./run.sh doctors` — it prints warnings for missing
`location`, missing `keywords.core`, or empty `titles.also`.