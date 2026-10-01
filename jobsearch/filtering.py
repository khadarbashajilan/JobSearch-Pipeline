"""Local post-filtering of combined scraped results.

Applies the date window and title exclusions once, locally, to every site so
behaviour is identical across boards — a deliberate uniformity choice, not a
patch for a broken board API.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def post_filter(df, hours_old: int | None, exclude: list[str]):
    """Apply the date window and exclude words to every site's results.

    A deliberate uniformity choice, not a patch for a broken board API. Both boards
    handle server-side filtering inconsistently: LinkedIn ignores -X outright, and
    Indeed's -X works but over-narrows a thin market (five excludes took 8 rows to 1
    for Bengaluru at 72h). date_posted and title come back reliably from every site,
    so filtering once, locally, is the only approach that behaves the same everywhere.
    """
    import pandas as pd

    if df.empty:
        return df, {"dated_out": 0, "excluded_out": 0}

    out = df
    stats = {"dated_out": 0, "excluded_out": 0}

    if hours_old:
        cutoff = datetime.now() - timedelta(hours=int(hours_old))
        posted = pd.to_datetime(out["date_posted"], errors="coerce")
        keep = posted.isna() | (posted >= cutoff)
        stats["dated_out"] = int((~keep).sum())
        out = out[keep]

    if exclude and not out.empty:
        terms = [e.lower() for e in exclude if e]
        titles = out["title"].astype(str).str.lower()
        keep = ~titles.apply(lambda t: any(term in t for term in terms))
        stats["excluded_out"] = int((~keep).sum())
        out = out[keep]

    return out.reset_index(drop=True), stats