"""The version-1 ``usage.db`` schema (before story #187 added ``os_user``).

Derived from the current ``SCHEMA`` by dropping the one column version 2 added,
and verified against the hash pinned for version 1, so a pre-upgrade database
built from it is faithful to what the previous release created.
"""

import hashlib

from pacemaker import database

V1_SCHEMA_SHA256 = "6e2addf3a441e5ea3c09f183f297317b94404b60b0f2618d78ee34af466bef0c"


def v1_schema() -> str:
    schema = database.SCHEMA.replace(",\n    os_user TEXT", "")
    assert (
        hashlib.sha256(schema.encode("utf-8")).hexdigest() == V1_SCHEMA_SHA256
    ), "SCHEMA drifted: the derived v1 schema no longer matches the shipped one"
    return schema
