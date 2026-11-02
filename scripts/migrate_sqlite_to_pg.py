"""One-time copy of the old local SQLite file into the Postgres database in DATABASE_URL.

    .venv/bin/python scripts/migrate_sqlite_to_pg.py            # reads ./opportunities.db
    .venv/bin/python scripts/migrate_sqlite_to_pg.py path/to.db

Copies opportunities (keeping ids, so swipes still line up), eligibility_checks, swipes, submissions
and users/sessions if any. Safe to re-run: rows are upserted by primary key.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from models import connect  # noqa: E402

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "opportunities.db")
TABLES = ["opportunities", "eligibility_checks", "users", "sessions", "swipes", "submissions", "login_codes"]
PK = {"eligibility_checks": ("source", "source_id"), "sessions": ("token_hash",)}

src = sqlite3.connect(SRC)
src.row_factory = sqlite3.Row
with connect() as pg:
    for t in TABLES:
        try:
            rows = src.execute(f"SELECT * FROM {t}").fetchall()
        except sqlite3.OperationalError:
            print(f"{t}: not in the SQLite file, skipped")
            continue
        if not rows:
            print(f"{t}: 0 rows")
            continue
        cols = rows[0].keys()
        pk = PK.get(t, ("id",))
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in pk)
        sql = (f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('%s' for _ in cols)}) "
               f"ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {updates}")
        with pg.cursor() as cur:
            cur.executemany(sql, [tuple(r) for r in rows])
        if "id" in cols:   # move the sequence past the copied ids
            pg.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), (SELECT COALESCE(MAX(id), 1) FROM {t}))")
        print(f"{t}: {len(rows)} rows")
print("done")
