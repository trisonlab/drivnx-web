# [DEBUG-5b9d] throwaway probe; delete after debugging.
import collections
import threading

import pytest
from connectors import postgres
from connectors.csv_files import duckdb_connection

from .. import postgres_fixture
from .test_postgres_stopping import (  # noqa: F401
    SCHEMA, CANCEL_WITHIN_SECONDS, DroppingProxy, drivnx_queries, objects, reading,
)

SEEN: collections.Counter = collections.Counter()


@pytest.mark.parametrize(
    ("tls_mode", "attempt"),
    [*(("disable", n) for n in range(150)), *(("prefer", n) for n in range(30))],
)
def test_probe(tmp_path, drivnx_queries, monkeypatch, tls_mode, attempt):
    raw: list[str] = []
    original = postgres._not_read

    def recording(error, *args):
        raw.append(str(error))
        return original(error, *args)

    monkeypatch.setattr(postgres, "_not_read", recording)
    proxy = DroppingProxy()
    details = postgres.ConnectionDetails(
        host="127.0.0.1", port=proxy.port, database=postgres_fixture.DATABASE,
        user=postgres_fixture.USER, tls_mode=tls_mode,
    )
    failures = []
    with duckdb_connection(tmp_path) as connection:
        postgres.attach(connection, details, postgres_fixture.PASSWORD)

        def read():
            try:
                postgres.load_whole_object(connection, (SCHEMA, "late_sleepy"), ("n",), "rows")
            except BaseException as error:  # noqa: BLE001
                failures.append(error)

        reader = threading.Thread(target=read)
        reader.start()
        reading(drivnx_queries, f'COPY (SELECT "n" FROM "{SCHEMA}"."late_sleepy"%')
        proxy.drop()
        reader.join(CANCEL_WITHIN_SECONDS)
    reason = getattr(failures[0], "reason", repr(failures)) if failures else None
    text = " | ".join(raw).replace("\n", "\\n")
    print(f"\n[DEBUG-5b9d] tls={tls_mode} reason={reason} raw={text[:400]}")
