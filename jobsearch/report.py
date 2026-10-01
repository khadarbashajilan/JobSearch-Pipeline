"""Markdown report rendering.

Builds the grouped report of clickable job links, recording the exact query
used per site so a thin result set can be diagnosed later by reading which
query produced it.
"""

from __future__ import annotations

from datetime import datetime

from jobspy.model import Site

from jobsearch.scraper import site_counts


def md_escape(value: object) -> str:
    """Escape a value for safe use inside a markdown line.

    Job titles and company names come from scraped HTML, so they routinely
    contain [ and ] which would otherwise be read as a link by any markdown
    renderer. Backslashes are escaped first so the added backslashes are not
    themselves re-escaped.
    """
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("\r", " ")
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def cell(value: object) -> str:
    """Render one dataframe cell as markdown-safe text.

    Missing values come back as None or NaN depending on the column dtype, and
    str(NaN) is the literal "nan", so both are normalized to an empty string
    here rather than leaking "nan" into the report.
    """
    if value is None:
        return ""
    try:
        if value != value:
            return ""
    except TypeError:
        pass
    return md_escape(value).strip()


def render(df, cfg: dict, queries_by_site: dict[str, str], stats: dict) -> str:
    """Build the markdown report.

    The report records the exact query used per site alongside the links, so a
    thin result set can be diagnosed later by reading which query produced it —
    the file doubles as a record of what was searched.
    """
    stamp = datetime.now()
    header = f"**Location:** {cfg['location']} · **Country:** {cfg['country']}"
    if cfg["hours_old"]:
        header += f" · **Freshness:** last {cfg['hours_old']}h"

    lines = [
        f"# Job Links — {stamp:%Y-%m-%d %H:%M}",
        "",
        header,
        "",
        f"**Sites:** {', '.join(s.value for s in cfg['sites'])}",
        "",
    ]

    counts = site_counts(df, cfg)
    empty = [s for s, n in counts.items() if n == 0]
    lines.append(
        "**"
        + " · ".join(f"{s}: {n}" for s, n in counts.items())
        + f"** — {len(df)} unique jobs"
    )
    lines.append("")

    applied = []
    if cfg["hours_old"]:
        applied.append(f"last {cfg['hours_old']}h")
    if cfg["exclude"]:
        applied.append("dropped " + "/".join(cfg["exclude"]) + " from titles")
    if applied:
        lines += [f"Filtered locally: {' · '.join(applied)}.", ""]

    if empty:
        lines += [
            f"> No results from: {', '.join(empty)}. Job boards throttle and block "
            "aggressively, so an empty site is often transient — rerun, or check "
            "`-v 2` for the board's own error.",
            "",
        ]

    lines += ["## Queries", ""]
    for site in cfg["sites"]:
        lines.append(f"- `{site.value}` → `{queries_by_site[site.value]}`")
    lines.append("")
    if Site.INDEED in cfg["sites"]:
        lines += [
            "> The date window and exclude words above are applied **locally**, to "
            "every site. LinkedIn ignores `-X` terms outright, and on Indeed each "
            "exclusion sharply narrows an already thin pool — so job titles are "
            "filtered here instead. If Indeed looks thin, widen `queries.indeed` in "
            "resume.md; that is the knob that opens the pool up.",
            "",
        ]

    if df.empty:
        lines += [
            "## No results",
            "",
            "Every requested site returned nothing. Common causes, in order of "
            "likelihood: the query is too narrow for this location, Indeed throttled "
            "you, or the board is blocking this IP (Naukri 406, ZipRecruiter/Bayt 403, "
            "Glassdoor 400, BDJobs empty).",
        ]
        return "\n".join(lines) + "\n"

    for site in sorted(counts):
        if counts[site] == 0:
            continue
        group = df[df["site"] == site]
        lines.append(f"## {site} ({counts[site]})")
        lines.append("")
        for _, row in group.iterrows():
            title = cell(row.get("title")) or "untitled"
            url = cell(row.get("job_url"))
            lines.append(f"- [{title}]({url})")
            bits = [
                b for b in (cell(row.get("company")), cell(row.get("location"))) if b
            ]
            posted = cell(row.get("date_posted"))
            if bits:
                lines.append(
                    f"  {' · '.join(bits)}" + (f" · {posted}" if posted else "")
                )
            elif posted:
                lines.append(f"  {posted}")
            lines.append("")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"