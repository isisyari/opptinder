"""Pull every source into the database (DATABASE_URL).

    python3 ingest.py            # all sources
    python3 ingest.py devpost    # one source
    ENRICH=1 python3 ingest.py   # also read new rows' official pages with Claude (needs ANTHROPIC_API_KEY)

In production the nightly GitHub Actions workflow (.github/workflows/ingest.yml) runs this against
the hosted database, so the site refreshes without your laptop or the web server being involved.
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone

from models import connect, upsert, expire_past_deadlines
from sources import ALL, aggregators

# Sources whose fetch() legitimately returns [] (a local file) rather than failing: an empty result
# means "retire everything I listed before". Scrapers are exempt because a transient empty page
# must not wipe their rows.
ALWAYS_RETIRE = {"manual"}


def _disabled(name: str) -> bool:
    """Opt-in sources that are switched off must not keep serving stale rows."""
    return name == "pathways" and os.environ.get("PATHWAYS_OK") != "1"


def run(names: list[str] | None = None, log=print, enrich: bool = False) -> dict:
    """Ingest the named sources (all by default). Returns a summary dict and never raises for a
    single failing source. `log` receives one line per source. With enrich=True (and an Anthropic
    credential available) new rows then get their official page read by enrich.py."""
    summary = _ingest(names, log)
    if enrich:
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            log("[enrich] skipped: no ANTHROPIC_API_KEY in the environment")
            summary["enrich"] = {"skipped": "no credential"}
        else:
            try:
                import enrich as enrich_mod
                summary["enrich"] = enrich_mod.run(log=log)
            except Exception as e:
                log(f"[enrich] FAILED: {e}")
                summary["enrich"] = {"error": f"{type(e).__name__}: {e}"}
    return summary


def _ingest(names: list[str] | None, log) -> dict:
    aggregators.reset()              # cross-site title dedupe is per run, not per process
    summary: dict = {"sources": {}, "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for name in names or list(ALL):
        if name not in ALL:
            log(f"[{name}] unknown source, skipping")
            summary["sources"][name] = {"error": "unknown source"}
            continue
        run_started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if _disabled(name):
            with connect() as conn:
                hidden = conn.execute(
                    "UPDATE opportunities SET status='hidden' WHERE source=%s AND status='active'", (name,)
                ).rowcount
            log(f"[{name}] disabled (opt-in); hid {hidden} rows")
            summary["sources"][name] = {"disabled": True, "hidden": hidden}
            continue
        try:
            items = ALL[name]()                 # network work happens outside any transaction
        except Exception as e:
            log(f"[{name}] FAILED: {e}")
            log(traceback.format_exc())
            summary["sources"][name] = {"error": f"{type(e).__name__}: {e}"}
            continue
        with connect() as conn:                 # one transaction per source
            for o in items:
                upsert(conn, o)
            # Anything this source used to list but no longer does has closed. Curated rows are exempt
            # unless the curated file itself is the source being refreshed.
            gone = 0
            if items or name in ALWAYS_RETIRE:
                gone = conn.execute(
                    "UPDATE opportunities SET status='expired' WHERE source=%s AND status='active' AND last_seen < %s"
                    + ("" if name in ALWAYS_RETIRE else " AND verified=0"),
                    (name, run_started),
                ).rowcount
        log(f"[{name}] upserted {len(items)}" + (f", retired {gone} no longer listed" if gone else ""))
        summary["sources"][name] = {"upserted": len(items), "retired": gone}
    with connect() as conn:
        expired = expire_past_deadlines(conn)
        total = conn.execute("SELECT COUNT(*) AS n FROM opportunities WHERE status='active'").fetchone()["n"]
        by_flag = {r["k"]: r["n"] for r in conn.execute(
            "SELECT f1_flag AS k, COUNT(*) AS n FROM opportunities WHERE status='active' GROUP BY f1_flag")}
    log(f"expired {expired}; active {total}; by f1_flag: " + ", ".join(f"{k}={v}" for k, v in by_flag.items()))
    summary.update(expired=expired, active=total, by_flag=by_flag,
                   finished=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return summary


def main(names: list[str]) -> None:
    run(names or None, enrich=os.environ.get("ENRICH") == "1")


if __name__ == "__main__":
    main(sys.argv[1:])
