"""One OS user's simulated hook traffic against the shared data dir (#187).

Runs inside the container as a real OS user. Uses the real pacemaker code from
the read-only /src mount; PACEMAKER_TEST_MODE must be unset so production
path resolution (config.json -> shared_data_dir) is exercised.
"""

import sys
from datetime import datetime, timezone

sys.path.insert(0, "/src")

from pacemaker import database as db  # noqa: E402
from pacemaker.data_dir import monitoring_db_path, os_user  # noqa: E402
from pacemaker.secrets import database as sdb  # noqa: E402
from pacemaker.session_registry import registry  # noqa: E402

iterations = int(sys.argv[1])
usage = monitoring_db_path("usage.db")
registry_db = monitoring_db_path("session_registry.db")
failures = 0

if not db.initialize_database(usage):
    failures += 1
for i in range(1, iterations + 1):
    sid = f"{os_user()}-{i}"
    ok = db.record_activity_event(usage, "IV", "green", sid)
    ok &= db.insert_usage_snapshot(
        usage, datetime.now(timezone.utc), 1.0, None, 2.0, None, sid
    )
    ok &= db.record_blockage(usage, "other", "r", "stop", sid)
    db.should_poll_globally(usage, 300, sid)
    registry.heartbeat_and_purge(sid, "/ws", i, registry_db)
    sdb.create_secret(usage, "text", f"secret-{os_user()}-{i}-xxxxxxxx")
    if not ok:
        failures += 1
print(f"{os_user()} failures={failures}")
sys.exit(1 if failures else 0)
