# JobSearch-Pipeline

A job-search automation tool I built for my own daily use. It reads a
machine-readable search profile from a local `resume.md`, derives the right
query for each job board, scrapes multiple boards concurrently, filters
results locally so behaviour is identical everywhere, and writes a grouped
markdown report of clickable links.

Scraping itself is handled by the open-source
[JobSpy](https://github.com/cullenwatson/JobSpy) library. Everything in this
repo that is **not** `jobspy/` is the automation layer I built on top of it:
profile parsing, query derivation, local filtering, resilience, and reporting.

## What I actually built

This started as a clone of JobSpy. What I added on top:

1. **A config profile.** The search profile lives in a TOML block in
   `resume.md` — one editable source of truth for titles, location, keywords,
   freshness, and per-site query overrides. No hardcoding in code, no
   inference at runtime; behaviour is predictable and reviewable.
2. **Per-site query derivation.** Indeed gets a boolean query
   (`"GenAI Developer" (Python OR FastAPI OR LLM)`); every other board gets
   plain keywords. `queries.indeed` in the profile overrides the derived form.
3. **Local, uniform filtering.** Job boards implement filters inconsistently,
   so I apply the date window and title exclusions **once, locally**, to the
   combined results — every site behaves the same.
4. **Resilience.** Retry with backoff for flaky/throttled boards, per-site
   counts in the report (including zeros), deduplication on `job_url`, and a
   `--dry-run` mode to validate queries without touching the network.
5. **A report that records its own queries.** Each `saved/job-links-*.md`
   shows the exact query per site next to the links, so a thin result set can
   be diagnosed later.

The profile-maintenance workflow is captured in an opencode skill at
`.opencode/skills/job-search-profile/SKILL.md` (committed in this repo). The
`resume.md` itself is gitignored — it contains personal contact details — but
the skill file documents the full profile contract and how it is maintained
(see [Maintaining the profile](#maintaining-the-profile-with-the-opencode-skill)).

## Usage

```bash
./run.sh                # scrape using the profile in resume.md
./run.sh --dry-run      # print the derived queries, scrape nothing
./run.sh --site indeed,linkedin --results 50
```

`run.sh` creates the virtualenv and installs the package on first run. Reports
are written to `saved/job-links-<timestamp>.md`.

Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | Print the profile and derived queries, then exit without scraping |
| `--site` | Comma-separated sites (default `indeed,linkedin`) |
| `--location` / `--country` | Override the profile's search location |
| `--results` | Override `results_per_site` |
| `--hours-old` | Override the freshness window |
| `--retries` | Retry count for a site group returning nothing (default 1) |
| `-v 0\|1\|2` | Verbosity: errors only / +warnings / all logs |

## How it works

```
resume.md (toml profile)
        │
        ├─ load_profile + validate ──► fail fast on a malformed profile
        │
        ├─ build_queries ────────────► boolean query for Indeed,
        │                              plain keywords for every other board
        │
        ├─ scrape_all ───────────────► one concurrent call per query form,
        │                              retry with backoff, dedup on job_url
        │
        ├─ post_filter ──────────────► date window + title exclusions,
        │                              applied locally to every site
        │
        └─ render ───────────────────► saved/job-links-<timestamp>.md
```

### The profile

`resume.md` contains one fenced `toml` block under `## Job Search Profile`:

```toml
[titles]
primary = "GenAI Developer"
also    = ["Generative AI Engineer", "AI Engineer"]

[search]
location         = "Bengaluru, India"
country          = "India"
hours_old        = 72
results_per_site = 30
indeed_date_filter = false

[keywords]
core    = ["Python", "FastAPI", "LangChain", "RAG", "LLM", "React"]
exclude = ["Senior", "Lead", "Principal", "Staff", "Manager"]

[queries]
indeed = '"Gen AI" (Python OR FastAPI OR LLM)'
```

| Field | Required | Meaning |
|---|---|---|
| `titles.primary` | yes | Best-fit title. Becomes the quoted phrase in the Indeed query |
| `titles.also` | no | Alternative titles, for reference |
| `search.location` | yes | Free text; commas are safe because it is TOML |
| `search.country` | no | Indeed/Glassdoor country. Must match a `Country` value |
| `search.hours_old` | no | Keep jobs posted within N hours. Applied locally |
| `search.results_per_site` | no | Target count per site (default 10) |
| `search.indeed_date_filter` | no | Also pass the window to Indeed's own filter. Off by default |
| `keywords.core` | yes (min 2) | Ordered by significance. First 6 reach the query |
| `keywords.exclude` | no | Dropped from job titles locally, on every site |
| `queries.indeed` | no | Overrides the derived Indeed query |

TOML rather than comma-separated prose because values legitimately contain
commas (`location = "Bengaluru, India"`) and quotes.

### Maintaining the profile with the opencode skill

The repo ships an [opencode](https://opencode.ai) skill at
`.opencode/skills/job-search-profile/SKILL.md` that keeps the TOML profile in
`resume.md` in sync with your actual resume.

Use it by asking opencode to update your job search profile — for example,
*"update my job search profile"*, *"refresh my job targets"*, or *"what will
run.sh search for"*. The skill will:

1. Read `resume.md` in full.
2. Rewrite **only** the fenced `toml` block under `## Job Search Profile`,
   leaving the rest of the resume untouched.
3. Never invent skills or titles the resume does not evidence — if your actual
   target differs from what the resume shows, it asks before changing anything.
4. Derive titles, location, keywords, and exclusions from the resume content,
   keeping `keywords.core` at ~6 entries in descending significance.
5. Report what changed and the exact queries it will produce per site.

Verify any change without touching a job board:

```bash
./run.sh --dry-run    # prints the derived queries, scrapes nothing
```

## Design notes

These were measured against the live boards, not assumed.

**LinkedIn ignores `-X` exclusions.** A query containing `-Senior` still
returns "Senior AI Engineer". Verified repeatedly.

**Indeed's `-X` works but over-narrows.** Same title and window, Bengaluru:

| query | rows | senior |
|---|---|---|
| `GenAI Developer` | 9 | 5 |
| + 3 `OR` terms | 8 | 4 |
| + 6 `OR` terms | 8 | 4 |
| + 6 `OR` terms + 5 excludes | **1** | **0** |

The exclusions do exactly what they should — zero senior rows — but five of
them turned 8 rows into 1. Hence excludes are filtered locally, not sent as
`-X` terms, and `queries.indeed` carries no excludes.

**Indeed's `OR` group barely matters.** 0 / 1 / 3 / 6 terms gave 9 / 8 / 8 / 8
rows. Not worth lengthening.

**Indeed's server-side date filter works correctly** (`72h → 9`, `168h → 36`,
`720h → 100`, monotonic). It is off by default only to keep one local rule
across all sites.

**Boards throttle hard and return empty frames rather than errors.** Identical
Indeed queries returned 5 rows one minute and 0 the next. Hence the retry with
backoff, and per-site counts in the report including zeros — a blank site is
visible rather than silent.

**Blocked from many IPs, not broken.** Naukri returns `406`, ZipRecruiter and
Bayt `403`, Glassdoor `400`, BDJobs returns nothing. Defaults are
`indeed,linkedin` for that reason.

## Credits

Board scraping comes from [JobSpy](https://github.com/cullenwatson/JobSpy) by
Cullen Watson and contributors, MIT licensed. See `LICENSE`.