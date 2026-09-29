"""Create the schema and prove the database is actually usable.

Run: uv run python scripts/init_db.py
"""

import sys
from pathlib import Path

import psycopg

from kzbank.config import settings
from kzbank.logging_setup import setup_logging
from kzbank.storage.db import connect, table_counts

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "src" / "kzbank" / "storage" / "schema.sql"

# Must match the vector(N) declaration in schema.sql.
SCHEMA_EMBED_DIM = 1024


def check_dimension_agreement() -> None:
    """Refuse to run if .env and schema.sql disagree about the vector size."""
    if settings.embed_dim != SCHEMA_EMBED_DIM:
        raise SystemExit(
            f"EMBED_DIM is {settings.embed_dim} but schema.sql declares "
            f"vector({SCHEMA_EMBED_DIM}).\n"
            f"Change one of them — a mismatch makes every chunk insert fail."
        )


def apply_schema() -> None:
    """Apply schema.sql. Safe to run repeatedly."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    # with_vector=False: the `vector` type does not exist until this SQL runs.
    with connect(with_vector=False) as conn, conn.cursor() as cur:
        cur.execute(sql)


def report() -> None:
    """Print what actually exists in the database."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT version() AS v")
        version = (cur.fetchone() or {}).get("v", "?")

        cur.execute("SELECT extversion AS v FROM pg_extension WHERE extname = 'vector'")
        row = cur.fetchone()
        if row is None:
            raise SystemExit("pgvector extension is missing — is this the pgvector image?")
        vector_version = row["v"]

    print(f"  postgres  {str(version).split(' on ')[0]}")
    print(f"  pgvector  {vector_version}  OK")
    print()
    for table, n in table_counts().items():
        print(f"  {table:<12} {n:>6} rows")


def main() -> int:
    setup_logging()
    print(f"\nkzbank — database init\n  target    {settings.database_url}\n")

    check_dimension_agreement()

    try:
        apply_schema()
    except psycopg.OperationalError as e:
        raise SystemExit(
            f"Cannot reach Postgres at {settings.database_url}\n"
            f"  {e}\n"
            f"Is it running?  docker-compose up -d db"
        ) from e

    report()
    print("\n  schema applied.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
