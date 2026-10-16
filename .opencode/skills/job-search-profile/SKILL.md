---
name: job-search-profile
description: >
  Keep resume.md — the résumé prose AND the machine-readable "## Job Search Profile"
  toml block that ./run.sh reads — in sync with the user's real résumé. Trigger when
  the user pastes résumé text (often copied from their résumé PDF) and asks to
  update resume.md; when they add/remove a title, skill, keyword, location or board;
  when they change seniority ("only junior roles", "entry-level only", "strict",
  "include mid-level", "no level ceiling"); when they change where to search
  ("anywhere in South India", "add Pune", "remote only"); when they say "update my
  job search profile", "refresh my job targets"; or when ./run.sh reports a profile
  validation error. A pasted résumé refreshes the prose and the résumé-derived facts
  in the toml; search preferences are never changed unless the user asks. Never
  invents skills the résumé does not evidence.
---

## What this does

`./run.sh` scrapes job boards and ranks jobs using `resume.md`. Two parts of that
file are read by the code:

1. **The `toml` block under `## Job Search Profile`** — every search setting.
2. **The résumé's skills section** (the `## ` heading named by
   `skills.resume_section`, default `Technical Skills`) — the matcher reads its
   bullets as the candidate's skills.

Everything else in the prose is for humans. No model runs at search time; this
skill is the only place judgement is applied, so keep the file honest.

## Workflow A — the user pastes an updated résumé

This is the common case: the user updates their résumé PDF, copies all of its
text, and pastes it here.

1. **Read `resume.md` in full first**, including the toml block. You need the
   current preferences before touching anything.
2. **Rewrite the prose** (everything *above* `---` / `## Job Search Profile`) from
   the pasted text:
   - Clean PDF copy artefacts: re-join lines broken mid-sentence, turn `●`, `•`,
     `▪`, `–` bullets into `- `, fix ligatures (`ﬁ` → `fi`), drop page numbers and
     repeated headers/footers.
   - Structure: `# Name`, a contact line, then `## ` sections (Professional
     Summary, Technical Skills, Experience, Projects, Education, Certifications —
     whatever the paste has), `### ` for each role/project, `- ` bullets.
   - Keep the user's wording. Do not polish, embellish, add metrics, or add
     skills. Reordering and formatting only.
   - **The skills section must stay parseable:** a `## ` heading whose name equals
     `skills.resume_section`, with one bullet per group in the form
     `- **Label:** Skill, Skill (qualifier), Skill`. If the pasted résumé renames
     the heading (e.g. "Skills"), either keep the old heading name or update
     `skills.resume_section` to match — never leave them out of sync.
   - Contact details are fine here: `resume.md` is gitignored. Never commit it.
3. **Leave the `## Job Search Profile` section in place** (header, the one-line
   human summary, the `>` note, the toml block). Then update **only the
   résumé-derived (FACT) keys** in the toml — see the table below. Do not touch
   PREFERENCE keys.
4. **Validate and show the result** (see *Verifying a change*). Report:
   what prose sections changed, which FACT keys changed (old → new), skills the
   matcher now reads that it did not before (and vice versa), and anything you
   were unsure about (e.g. a date range you could not read).
5. If the pasted résumé suggests a **different target** (new primary title,
   different stack, more seniority), say so and **ask** before changing titles,
   `keywords.core`, or the level band. Do not quietly re-target the search.

### FACT keys (refresh from the résumé)

| Key | How to derive it |
|---|---|
| `keywords.core` | ~6 strongest skills from the résumé, most significant first. Only change if the résumé's emphasis really changed. |
| `skills.also` | Spellings/skills the résumé evidences but the skills section doesn't spell the way job posts do (`Postgres` for PostgreSQL, `AWS` for AWS EC2, `Next.js` used in a project). **Every entry must appear somewhere in the résumé.** Drop entries the new résumé no longer evidences. |
| `skills.watch` | Remove any entry the candidate now has (it would show as "missing" forever). Do not add new ones on a paste — that list is the user's call. |
| `experience.years` | Total months of professional work (full-time + internships in relevant roles) up to today, ÷ 12, rounded to the nearest 0.5. "Present" = today. State the months you counted. |
| `titles.primary` / `titles.also` | Only with the user's OK (step 5). |

### PREFERENCE keys (never change on a paste — only when the user asks)

`search.*` (locations, allowed_regions, keep_remote, keep_unknown_location, sites,
parallel_sources, hours_old, results_per_site, remote, employment_types, retries,
retry_backoff_seconds, linkedin_fetch_description, indeed_date_filter),
`keywords.exclude`, `keywords.exclude_stack`, `keywords.level_min`,
`keywords.level_filter`, `keywords.level_strict`, `experience.level`,
`experience.max_required_years`, `levels.aliases`, `verify.*`, `report.*`,
`matching.*`, `queries.*`, `llm.*`, `skills.resume_section` (except to follow a
renamed heading, step 2).

## Workflow B — the user changes a preference

Edit only the keys involved, keep comments, run `./run.sh --dry-run`, and show the
lines that changed. Common requests:

| User intent | Setting |
|---|---|
| *"junior / entry-level only"* | `level_min = "junior"`, `level_filter = "junior"` |
| *"…strictly"*, *"only jobs that say junior/entry-level"* | also `level_strict = true` |
| *"also show jobs that don't state a level"* | `level_strict = false` |
| *"include internships"* | `level_min = "intern"` (or `"off"`); add `"internship"` to `employment_types` if they want intern contracts too |
| *"mid-level roles"*, *"2–5 years"* | `level_filter = "mid"`, `max_required_years = 5` |
| *"senior roles"*, *"senior+"* | `level_min = "senior"`, `level_filter = "off"`; remove Senior/Lead from `keywords.exclude` |
| *"no level filter"* | `level_min = "off"`, `level_filter = "off"`, `level_strict = false` |
| *"keep Software Engineer II roles"* | `[levels] aliases = { "ii" = "junior" }` |
| *"anywhere in <region>"* | `search.locations` = the region's main hubs (each one is a search per board); `search.allowed_regions` = its states, state codes (Indeed uses `KA`, `TN`, …), and city spellings (`Bengaluru`, `Bangalore`) |
| *"only <city>"* | `locations = ["<city>"]`, `allowed_regions = []` |
| *"remote only"* | `remote = "remote"` (on-site jobs become hard conflicts), `keep_remote = true` |
| *"search all boards"* | `sites` = all of `linkedin, indeed, naukri, glassdoor, google, zip_recruiter, bayt, bdjobs, greenhouse, lever, ashby`; warn that zip_recruiter / bayt / bdjobs rarely cover India and the three ATS feeds need `JOBSEARCH_<NAME>_BOARD` tokens |
| *"drop <stack> jobs"* | add to `keywords.exclude_stack` |
| *"it's too slow"* | fewer `locations`, fewer `sites`, lower `results_per_site`, or `linkedin_fetch_description = false` (LinkedIn jobs then lose skill/years scoring) |

Searches per run = boards × `locations` (+ 1 per ATS feed). Mention the new count
when a change multiplies it.

## The toml contract

Exactly one fenced ```` ```toml ```` block under `## Job Search Profile`. Required:
`titles.primary`, `search.location` **or** `search.locations`, `keywords.core`
(≥ 2). Everything else is optional. Shape (values illustrative):

```toml
[titles]
primary = "Full-Stack Developer"
also    = ["Full Stack Engineer", "Software Engineer"]

[search]
locations        = ["Bengaluru", "Chennai", "Hyderabad"]
country          = "India"
allowed_regions  = ["Karnataka", "KA", "Tamil Nadu", "TN", "Telangana", "TS", "Bangalore"]
keep_remote           = true
keep_unknown_location = true
sites            = ["linkedin", "indeed", "naukri", "glassdoor", "google"]
parallel_sources = 4
hours_old        = 72
results_per_site = 30
remote           = "any"            # onsite | remote | hybrid | any
employment_types = ["full_time"]    # full_time, part_time, contract, temporary, internship, other
indeed_date_filter = false
retries               = 1
retry_backoff_seconds = 12
linkedin_fetch_description = true

[keywords]
core          = ["Python", "FastAPI", "React", "TypeScript", "PostgreSQL", "Docker"]
exclude       = ["Senior", "Lead", "Principal", "Staff", "Manager"]
exclude_stack = ["Angular", "Java", ".NET"]
level_min     = "junior"
level_filter  = "junior"
level_strict  = true

[skills]
resume_section = "Technical Skills"
also  = ["Postgres", "AWS"]
watch = ["Java", "Kubernetes", "Node.js"]

[experience]
years              = 0.5
level              = "junior"
max_required_years = 2

[levels]
aliases = { "specialist" = "senior" }

[verify]
enabled = true
workers = 8
timeout = 8
uncertain_hosts = ["linkedin.com", "indeed.com"]
login_markers   = ["authwall", "login"]
expired_phrases = ["no longer accepting applications"]

[report]
previously_seen = "compact"   # full | compact | hide

[matching]
min_score = 0.0

[queries]
indeed = 'Full-Stack (Python OR React OR TypeScript OR FastAPI OR PostgreSQL)'
```

### Field semantics

| Field | Meaning |
|---|---|
| `titles.primary` | Single best-fit title. Quoted phrase in the derived Indeed query, leading term elsewhere. Pick what the bulk of the résumé supports. |
| `titles.also` | Alternative titles, folded into the plain query. Short, ordered by relevance. |
| `search.location` / `search.locations` | One location, or a list; every board is searched once per entry (ATS feeds once). Commas inside a value are fine. |
| `search.country` | JobSpy country (`India`, `USA`, `UK`, … — see `jobspy/model.py`). Default `USA`. |
| `search.allowed_regions` | Local location filter: keep a job only if its location names one of these as a whole word. Empty = keep all. |
| `search.keep_remote` / `search.keep_unknown_location` | Default `true`: remote jobs, and jobs with no location or only the country, pass the region filter. |
| `search.sites` | Sites searched when `--site` is omitted. Default `["indeed", "linkedin"]`. |
| `search.parallel_sources` | Searches run at once. Default 1. |
| `search.hours_old` | Keep jobs posted within N hours, applied locally to every site. Undated jobs are kept and labelled. |
| `search.results_per_site` | Target rows per search. Default 30. |
| `search.remote` | `onsite` / `remote` / `hybrid` / `any`. `remote` makes on-site-only jobs a hard conflict. |
| `search.employment_types` | Acceptable types. A job with another known type is a hard conflict (score 0). |
| `search.indeed_date_filter` | `true` also passes `hours_old` to Indeed's server filter (halves results). Default `false`. |
| `search.retries` / `search.retry_backoff_seconds` | Retry a search on an empty result or error, waiting `backoff × attempt`. Defaults 1 / 12. |
| `search.linkedin_fetch_description` | `true` fetches each LinkedIn job's description (slower) so skills, years and LinkedIn's level tag can be read. `doctors` warns when it is off. |
| `keywords.core` | Query terms, most significant first. The derived Indeed query uses the first 6; the plain query uses all, so keep it ~6. |
| `keywords.exclude` | Title words that drop a job (whole word, every site). |
| `keywords.exclude_stack` | Title technologies that drop a job (whole word: `Java` ≠ `JavaScript`). |
| `keywords.level_min` / `keywords.level_filter` | Floor / ceiling of the level band: `off`, `intern`, `junior`, `mid`, `senior`, `lead`, `staff`, `principal`. Level comes from the title, else the board's level field. Built-ins: Entry level / Fresher / Trainee / Graduate / Associate → junior; II → mid; III / Sr / Director / Head / VP → senior; IV → lead. |
| `keywords.level_strict` | `true` keeps only jobs with evidence they are in the band (a level word, the board's level field, or a years requirement ≤ `max_required_years`). `false` also keeps jobs that state nothing. |
| `skills.resume_section` | Résumé heading the matcher reads. Omitted = first heading containing "skill"; `""` = off. |
| `skills.also` | Extra skills/spellings the résumé evidences. |
| `skills.watch` | Technologies the candidate lacks; found in a posting they show as "missing" and lower the skill score. |
| `experience.years` | Years of experience (decimals allowed); scores a job's "N+ years". |
| `experience.level` | Candidate's own level; defaults to `level_filter`, then an estimate from `years`. |
| `experience.max_required_years` | Drop jobs whose title or description asks for more years (smallest stated requirement counts). |
| `levels.aliases` | Title phrase → level, merged over the built-ins; `"ignore"` disables a built-in. |
| `verify.*` | `enabled`, `workers`, `timeout`, `uncertain_hosts` (never fetched → `uncertain`), `login_markers` (sign-in redirect → `uncertain`), `expired_phrases`. |
| `report.previously_seen` | `compact` (new jobs first, earlier ones one line each), `full`, or `hide`. |
| `matching.min_score` | Hide jobs below this score (0–1). |
| `queries.indeed` | Overrides the Indeed query. The only place boolean syntax belongs. |
| `llm.provider` / `llm.model` | Reserved; unused. |

Preserve TOML typing (double-quoted strings, inline arrays, bare `true`/`false`) and
keep existing comments — they document why each value is what it is.

## How the queries are derived

`jobsearch/queries.py`: **Indeed** uses `queries.indeed` if set, otherwise
`"<titles.primary>" (<first 6 core joined by OR>) -<exclude…>`. **Every other
site** uses `titles.primary` + all `keywords.core` + all `titles.also`. Do not put
boolean operators in `keywords.core` or `titles.*`.

## Hard-won site constraints

Measured by probing the live boards (Bengaluru, radius 50, `hours_old = 72`):

- **Indeed's `-X` exclusions work but over-narrow:** 5 excludes took 8 rows to 1.
  That is why `queries.indeed` carries none and titles are filtered locally.
- **Indeed's `OR` group barely matters:** 0 / 1 / 3 / 6 terms → 9 / 8 / 8 / 8 rows.
- **LinkedIn ignores `-X` exclusions** — excludes are filtered locally.
- **Indeed's server `hours_old` filter works** (72h → 9, 168h → 36, 720h → 100);
  it is off by default only to keep one local rule for every site.
- **Indeed accepts only one of** `hours_old`, (`job_type` + `is_remote`), `easy_apply`.
- **Indeed is intermittently flaky** (5 rows, then 0, for the same query).
  `jobsearch/orchestrate.py` retries each search and reports per-search counts,
  including zeros — an empty Indeed line usually means throttling.
- **Blocked from many IPs, not broken:** Naukri `406 recaptcha required`,
  ZipRecruiter and Bayt `403`, Glassdoor `400`, BDJobs nothing. ZipRecruiter
  (US/Canada), Bayt (Gulf) and BDJobs (Bangladesh) also rarely list Indian jobs.
- **Greenhouse, Lever, Ashby** are public per-company JSON feeds, enabled with
  `JOBSEARCH_<NAME>_BOARD=<token>`; without a token the report lists them as skipped.

## Verifying a change

```bash
./run.sh inspect     # JSON of the parsed profile — check "skills" lists the résumé's skills section
./run.sh --dry-run   # resolved settings, derived queries, number of searches; scrapes nothing
./run.sh doctors     # warnings for settings that weaken results
```

Run all three after every edit; they need no job-board traffic. If `inspect` shows
no résumé skills, the skills heading and `skills.resume_section` are out of sync.
