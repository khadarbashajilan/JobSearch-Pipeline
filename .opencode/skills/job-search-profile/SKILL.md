---
name: job-search-profile
description: >
  Maintain the machine-readable job-search profile block in resume.md so ./run.sh
  scrapes correctly targeted listings. Use when the user edits their resume, adds or
  removes a target job title, skill, keyword, or target location, changes seniority
  level, or says "update my job search profile", "regenerate my job search profile",
  "refresh my job targets", "what will run.sh search for". Rewrites ONLY the toml
  block under "## Job Search Profile" in resume.md. Never edits the rest of the
  resume and never invents skills the resume does not evidence.
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
primary = "GenAI Developer"
also    = ["Generative AI Engineer", "AI Engineer", "LLM Engineer", "Python Developer"]

[search]
location         = "Bengaluru, India"
country          = "India"
hours_old        = 72
results_per_site = 10

[keywords]
core    = ["Python", "FastAPI", "LangChain", "RAG", "LLM", "React"]
exclude = ["Senior", "Lead", "Principal", "Staff", "Manager"]
```

### Field semantics

| Field | Required | Meaning |
|---|---|---|
| `titles.primary` | yes | Single best-fit title. Becomes the quoted phrase in the Indeed query and the leading word everywhere else. Pick the title that best matches the bulk of the resume, not the fanciest one. |
| `titles.also` | no | Alternative titles, for reference and future query fan-out. Not used by the current single-query runtime. Keep it short. |
| `search.location` | yes | Free text, e.g. `"Bengaluru, India"`. Commas are safe — that is the whole reason this is TOML and not comma-split prose. |
| `search.country` | no | Indeed/Glassdoor country. Must match a `Country` value in `jobspy/model.py` (e.g. `India`, `USA`, `UK`). Defaults to `USA`. Omit for sites that don't need it. |
| `search.hours_old` | no | Keep only jobs posted within N hours. `72` ≈ 3 days. Applied **locally** to `date_posted` for every site, so behaviour is identical across boards. |
| `search.results_per_site` | no | Target count per site. Default 10. Job boards cap out near 1000 per search. Raise it if post-filtering empties a site — over-fetching is cheap when you filter locally. |
| `search.indeed_date_filter` | no | Default `false`. When `true`, *also* passes `hours_old` to Indeed's own server-side `dateOnIndeed` filter. That filter works correctly, but it is redundant with the local filter and roughly halves the rows returned in a thin market. Turn it on only to cut fetch volume. |
| `keywords.core` | yes (min 2) | **Ordered by significance.** First 6 feed the plain query. Put the strongest signal first. |
| `keywords.exclude` | no | Dropped from job **titles** client-side. Applies to every site, LinkedIn included. |
| `queries.indeed` | no | Overrides the query sent to Indeed. Omit to derive it. See below. |

## How the queries are derived

`scrape.py` builds one query per site capability, then filters the combined results
locally by date and by title.

**Indeed** — `queries.indeed` if set, otherwise derived as
`"<titles.primary>" (<core…>) -<exclude…>`.
**Every other site** — `GenAI Developer Python FastAPI LangChain RAG LLM React`.

`keywords.exclude` is deliberately **not** sent to any board as a `-X` term, and the
date window is applied locally for every site. That is a uniformity choice, not a
patch for a broken API — the two reasons are in the constraints below: LinkedIn
ignores `-X` outright, and on Indeed each exclusion sharply narrows an already thin
pool. `date_posted` and `title` come back reliably from every site, so filtering once,
locally, is the only approach that behaves the same everywhere.

Do not put boolean operators into `keywords.core` or `titles.*`. Values are keywords
and titles, nothing else.

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
- **Dead code, deliberately left alone:** `dateOnIndeed=self.scraper_input.hours_old`
  passed to `.format()` at `jobspy/indeed/__init__.py:104` does nothing — the GraphQL
  template has no such placeholder (only `what`, `location`, `cursor`, `filters`).
  Harmless. Left unpatched to keep the vendored fork clean.
- **Indeed only accepts one of** `hours_old`, (`job_type` + `is_remote`), `easy_apply`.
- **Google ignores most parameters** if `google_search_term` is set, so the runtime
  passes `search_term` + `location` + `hours_old` and lets the scraper build the query.
- **Indeed is intermittently flaky** — identical queries returned 5 rows one minute
  and 0 the next. `scrape.py` retries a site group once and always reports per-site
  counts, including zeros, so a blank site is visible rather than silent. An empty
  Indeed section usually means throttling, not a bad profile.
- **Blocked from many IPs, not broken:** Naukri returns `406 recaptcha required`,
  ZipRecruiter and Bayt return `403`, Glassdoor returns `400`, BDJobs returns nothing.
  Defaults are `indeed,linkedin`. If a user wants another site, expect it may return
  zero rows — check the error log before assuming a profile bug.

## When to run this skill

Trigger on: resume content changed, a title/skill/location was added or removed, the
user asks what `run.sh` will search for, or the user reports `run.sh` failing with a
profile validation error.

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
   above, where five excludes took 8 rows to 1.
7. `queries.indeed` is the knob that widens the pool when Indeed looks thin. Widen the
   `OR` group or drop the quoted phrase there. Do not reach for `keywords.core` instead.
8. Report back: what changed, and the exact queries it produces per site.

## Verifying a change

```bash
./run.sh --dry-run     # prints the derived queries, scrapes nothing
```

`--dry-run` is cheap and needs no job board traffic. Use it after every edit.