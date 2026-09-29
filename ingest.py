"""Pull every source into opportunities.db.

    python3 ingest.py            # all sources
    python3 ingest.py devpost    # one source
"""
import sys
import traceback
from datetime import datetime, timezone

from models import connect, upsert, expire_past_deadlines
from sources import ALL


def main(names: list[str]) -> None:
    conn = connect()
    for name in names or ALL:
        run_started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            items = ALL[name]()
        except Exception:
            print(f"[{name}] FAILED")
            traceback.print_exc()
            continue
        for o in items:
            upsert(conn, o)
        # Anything this source used to list but no longer does has closed. Curated rows are exempt.
        gone = 0
        if items:
            gone = conn.execute(
                "UPDATE opportunities SET status='expired' WHERE source=? AND status='active' AND verified=0 AND last_seen < ?",
                (name, run_started),
            ).rowcount
        conn.commit()
        print(f"[{name}] upserted {len(items)}" + (f", retired {gone} no longer listed" if gone else ""))
    expired = expire_past_deadlines(conn)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM opportunities WHERE status='active'").fetchone()[0]
    by_flag = conn.execute(
        "SELECT f1_flag, COUNT(*) FROM opportunities WHERE status='active' GROUP BY f1_flag"
    ).fetchall()
    print(f"expired {expired}; active {total}; by f1_flag: " + ", ".join(f"{r[0]}={r[1]}" for r in by_flag))


if __name__ == "__main__":
    main(sys.argv[1:])
