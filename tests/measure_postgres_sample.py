"""Spike 2 of CFX-APP-postgres: how a fast profile should sample a large PostgreSQL table (ADR 0005).

`uv run --frozen python tests/measure_postgres_sample.py <extension dir> [host] [port]` against a
throwaway server whose `spike` user may create the `spike` database (the defaults are 127.0.0.1 and
54329). The extension directory receives DuckDB's postgres extension, downloaded once, outside
DrivnX, which never downloads one. `psql` must be on the path. It builds a 10-million-row table
once (about 790 MB, 30 s) and updates 1,000 of its rows. Not a test: pytest doesn't collect this,
as the figures depend on the machine.

The first 30% of the table's rows have other cities than the rest, so a sample that only sees part
of the table shows a different table. It times each way of sampling, whether a sample repeats and
how it is spread, a full profile, cancelling one, and discovery, and prints JSON.
"""

import json
import subprocess
import sys
import threading
import time

import duckdb

ROWS = 10_000_000
TARGET = 100_000
OLD_ROWS = 3_000_000
COLUMNS = ("id", "name", "city", "amount", "joined")
SETUP = f"""
CREATE TABLE big (
  id bigint PRIMARY KEY, name text NOT NULL, city text, amount numeric(10,2), joined date NOT NULL
);
INSERT INTO big
SELECT g,
       (array['Ann','Bob','Zoé','José','Cid','Dee'])[1 + (g * 7) % 6],
       CASE WHEN g % 10 = 0 THEN NULL
            WHEN g <= {OLD_ROWS} THEN (array['Paris','Lyon','Nice'])[1 + g % 3]
            ELSE (array['Orléans','Málaga','Metz','Tours'])[1 + g % 4] END,
       round(((g * 2654435761) % 100000) / 100.0, 2),
       date '2015-01-01' + (g * 3650 / {ROWS})::int
FROM generate_series(1::bigint, {ROWS}::bigint) g;
VACUUM ANALYZE big;
CREATE VIEW big_view AS SELECT * FROM big WHERE amount >= 0;
CREATE MATERIALIZED VIEW big_mat AS SELECT * FROM big WHERE id % 10 = 1;
CREATE TABLE part (id bigint, city text, joined date NOT NULL) PARTITION BY RANGE (joined);
CREATE TABLE part_a PARTITION OF part FOR VALUES FROM ('2015-01-01') TO ('2020-01-01');
CREATE TABLE part_b PARTITION OF part FOR VALUES FROM ('2020-01-01') TO ('2026-01-01');
INSERT INTO part SELECT id, city, joined FROM big WHERE id % 5 = 0;
ANALYZE big_mat;
ANALYZE part;
CREATE SCHEMA many;
SELECT format('CREATE TABLE many.t%s (id int PRIMARY KEY, a text, b bigint REFERENCES big (id))', g)
FROM generate_series(1, 1000) g \\gexec
"""
DISCOVER = {
    "objects": """
        SELECT n.nspname, c.relname, c.relkind, c.reltuples,
               has_table_privilege(c.oid, 'SELECT'),
               has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE')
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p', 'v', 'm') AND NOT c.relispartition
          AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
          AND n.nspname NOT LIKE 'pg_temp_%' AND n.nspname NOT LIKE 'pg_toast_temp_%'
    """,
    "columns": """
        SELECT a.attrelid, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
        FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'p', 'v', 'm')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
    """,
    "keys": """
        SELECT conrelid, contype, conkey, confrelid, confkey
        FROM pg_constraint WHERE contype IN ('p', 'u', 'f')
    """,
}


def main() -> None:
    extensions = sys.argv[1]
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    port = sys.argv[3] if len(sys.argv) > 3 else "54329"
    server = ["psql", "-h", host, "-p", port, "-U", "spike", "-v", "ON_ERROR_STOP=1", "-Atq"]

    def psql(sql: str, database: str = "spike") -> str:
        return subprocess.run(
            [*server, "-d", database, "-c", sql], capture_output=True, text=True, check=True
        ).stdout.strip()

    if not psql("SELECT 1 FROM pg_database WHERE datname = 'spike'", "postgres"):
        psql("CREATE DATABASE spike", "postgres")
    if not psql("SELECT to_regclass('public.big')"):
        subprocess.run([*server, "-d", "spike"], input=SETUP, text=True, check=True)

    install = duckdb.connect(config={"extension_directory": extensions})
    install.execute("INSTALL postgres")
    install.close()
    dsn = f"host={host} port={port} dbname=spike user=spike application_name=DrivnX"
    read_only = "-c default_transaction_read_only=on"

    def connect(options: str = read_only) -> duckdb.DuckDBPyConnection:
        connection = duckdb.connect(
            config={
                "extension_directory": extensions,
                "autoinstall_known_extensions": "false",
                "autoload_known_extensions": "false",
            }
        )
        connection.execute("SET enable_progress_bar = false")
        connection.execute("LOAD postgres")
        connection.execute(
            f"ATTACH '{dsn} options=''{options}''' AS pg (TYPE postgres, READ_ONLY)"
        )
        return connection

    def timed(work, repeat: int = 3) -> list[float]:
        runs = []
        for _ in range(repeat):
            started = time.monotonic()
            work()
            runs.append(round(time.monotonic() - started, 3))
        return runs

    con = connect()
    results: dict = {}
    started = time.monotonic()
    objects = con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE database_name = 'pg'"
    ).fetchone()[0]
    results["duckdb_catalog"] = {"objects": objects, "seconds": round(time.monotonic() - started, 3)}

    def sample_with(query: str) -> None:
        con.execute("DROP TABLE IF EXISTS sample")
        con.execute(f"CREATE TEMP TABLE sample AS {query}")

    def describe() -> dict:
        rows, fingerprint, old, cities, first, last = con.execute(
            f"SELECT count(*), sum(hash(id)), count(*) FILTER (WHERE id <= {OLD_ROWS}),"
            " count(DISTINCT city), min(id), max(id) FROM sample"
        ).fetchone()
        return {
            "rows": rows,
            "fingerprint": str(fingerprint),
            "share_from_first_30pct": round(old / rows, 3),
            "distinct_cities_of_7": cities,
            "min_id": first,
            "max_id": last,
        }

    def profile() -> None:
        """Roughly a CSV fast profile's work per column: counts, lengths, masked patterns."""
        for column in COLUMNS:
            cell = f"CAST({column} AS VARCHAR)"
            con.execute(
                f"SELECT count({cell}), count(DISTINCT {cell}), min(length({cell})),"
                f" max(length({cell})), avg(length({cell})) FROM sample"
            ).fetchall()
            con.execute(
                f"SELECT pattern, count(*) FROM (SELECT regexp_replace(regexp_replace({cell},"
                f" '[0-9]', '9', 'g'), '[^0-9\\s[:punct:]]', 'A', 'g') AS pattern FROM sample"
                f" WHERE {cell} IS NOT NULL) GROUP BY pattern ORDER BY 2 DESC LIMIT 10"
            ).fetchall()

    estimate = float(psql("SELECT reltuples FROM pg_class WHERE relname = 'big'"))
    percent = f"{TARGET / estimate * 100:.6f}"
    inside = "SELECT * FROM postgres_query('pg', 'SELECT * FROM public.big {}')"
    methods = {
        "limit": f"SELECT * FROM pg.public.big LIMIT {TARGET}",
        "limit_inside_postgres": inside.format(f"LIMIT {TARGET}"),
        "system": inside.format(f"TABLESAMPLE SYSTEM ({percent}) REPEATABLE (42)"),
        "bernoulli": inside.format(f"TABLESAMPLE BERNOULLI ({percent}) REPEATABLE (42)"),
        "duckdb_reservoir": f"SELECT * FROM pg.public.big USING SAMPLE {TARGET} ROWS",
    }
    for name, query in methods.items():
        load = timed(lambda: sample_with(query), 1 if name == "duckdb_reservoir" else 3)
        first = describe()
        sample_with(query)
        again = describe()
        results[name] = {
            "load_seconds": load,
            "profile_seconds": timed(profile, 1),
            "repeats_unchanged_table": first["fingerprint"] == again["fingerprint"],
            **first,
        }

    # Updating rows moves them, so a sample of an unchanged seed may still change.
    psql("UPDATE big SET amount = amount WHERE id % 10000 = 0")
    for name in ("limit", "system", "bernoulli"):
        sample_with(methods[name])
        results[name]["repeats_after_1000_updates"] = (
            describe()["fingerprint"] == results[name]["fingerprint"]
        )

    for relation in ("big_view", "big_mat", "part"):
        try:
            con.execute(
                f"SELECT count(*) FROM postgres_query('pg', 'SELECT * FROM public.{relation}"
                " TABLESAMPLE SYSTEM (1) REPEATABLE (42)')"
            ).fetchone()
            results[f"tablesample_on_{relation}"] = "works"
        except duckdb.Error as error:
            results[f"tablesample_on_{relation}"] = str(error).splitlines()[0][:200]
    results["view_limit_seconds"] = timed(
        lambda: sample_with(f"SELECT * FROM pg.public.big_view LIMIT {TARGET}")
    )

    aggregates = ", ".join(
        f"count({c}), count(DISTINCT {c}), min(length(CAST({c} AS VARCHAR))),"
        f" max(length(CAST({c} AS VARCHAR)))"
        for c in COLUMNS
    )
    full = f"SELECT {aggregates} FROM pg.public.big"
    in_postgres = aggregates.replace("VARCHAR", "text")
    one_thread = connect()
    one_thread.execute("SET threads = 1")
    results["full"] = {
        "one_pass_seconds": timed(lambda: con.execute(full).fetchall(), 2),
        "one_pass_one_thread_seconds": timed(lambda: one_thread.execute(full).fetchall(), 2),
        "same_aggregates_in_postgres_seconds": timed(
            lambda: con.execute(
                f"SELECT * FROM postgres_query('pg', 'SELECT {in_postgres} FROM public.big')"
            ).fetchall(),
            2,
        ),
        "duplicate_rows_seconds": timed(
            lambda: con.execute(
                "SELECT count(*) - (SELECT count(*) FROM (SELECT DISTINCT * FROM pg.public.big))"
                " FROM pg.public.big"
            ).fetchall(),
            1,
        ),
    }

    def running_on_server() -> int:
        return int(
            psql(
                "SELECT count(*) FROM pg_stat_activity WHERE application_name = 'DrivnX'"
                " AND state = 'active'"
            )
        )

    cancelling = connect()
    returned: list[float] = []

    def scan() -> None:
        try:
            cancelling.execute(full).fetchall()
        except duckdb.InterruptException:
            pass
        returned.append(time.monotonic())

    worker = threading.Thread(target=scan)
    worker.start()
    time.sleep(2)
    connections = running_on_server()
    interrupted = time.monotonic()
    cancelling.interrupt()
    worker.join(60)
    gone = None
    for _ in range(100):
        if running_on_server() == 0:
            gone = round(time.monotonic() - interrupted, 3)
            break
        time.sleep(0.1)
    results["cancel"] = {
        "server_sessions_while_scanning": connections,
        "returned_after_seconds": round(returned[0] - interrupted, 3),
        "server_queries_gone_after_seconds": gone,
    }

    limited = connect(f"{read_only} -c statement_timeout=2000")
    started = time.monotonic()
    try:
        limited.execute(full).fetchall()
        results["statement_timeout_2s"] = {
            "stopped_the_scan": False,
            "seconds": round(time.monotonic() - started, 3),
        }
    except duckdb.Error as error:
        results["statement_timeout_2s"] = {"stopped_the_scan": True, "error": str(error)[:200]}

    for label, sql in (
        ("duckdb_insert", "INSERT INTO pg.public.big VALUES (0, 'x', NULL, 0, DATE '2020-01-01')"),
        ("duckdb_create", "CREATE TABLE pg.public.probe (id INTEGER)"),
        ("postgres_execute", "CALL postgres_execute('pg', 'CREATE TABLE public.probe (id int)')"),
    ):
        try:
            con.execute(sql)
            results[f"write_{label}"] = "ALLOWED"
        except duckdb.Error as error:
            results[f"write_{label}"] = str(error).splitlines()[0][:200]

    for label, sql in DISCOVER.items():
        quoted = sql.replace("'", "''")
        query = f"SELECT count(*) FROM postgres_query('pg', '{quoted}')"
        results[f"discover_{label}"] = {
            "rows": con.execute(query).fetchone()[0],
            "seconds": timed(lambda: con.execute(query).fetchone()),
        }

    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
