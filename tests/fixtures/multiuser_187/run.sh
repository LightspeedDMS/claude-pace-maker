#!/usr/bin/env bash
# Runs INSIDE a throwaway container as root (#187 integration test).
# Two real OS users in one group share a setgid directory; both run the real
# pacemaker code concurrently with DIFFERENT umasks. Exit 0 only if no
# failures, no logged errors, files 0660 and both users' rows are present.
set -u
ITER="${1:-100}"
SHARED=/var/lib/claude-pace-maker
groupadd pmshared
for u in alice bob; do
  useradd -m -G pmshared "$u"
  install -d -o "$u" -g "$u" -m 700 "/home/$u/.claude-pace-maker"
  printf '{"enabled": true, "shared_data_dir": "%s"}' "$SHARED" \
    > "/home/$u/.claude-pace-maker/config.json"
  chown "$u:$u" "/home/$u/.claude-pace-maker/config.json"
done
mkdir -p "$SHARED"; chown root:pmshared "$SHARED"; chmod 2770 "$SHARED"

rc=0
(umask 077; runuser -u alice -- python3 /w/worker.py "$ITER") & p1=$!
(umask 022; runuser -u bob -- python3 /w/worker.py "$ITER") & p2=$!
wait $p1 || rc=1
wait $p2 || rc=1

echo "--- shared dir"; ls -l "$SHARED"
bad=$(find "$SHARED" -type f ! -perm 660 | wc -l)
echo "files_not_0660=$bad"
[ "$bad" = "0" ] || rc=1
errs=$(cat /home/*/.claude-pace-maker/*.log 2>/dev/null | grep -c 'ERROR\|WARNING')
echo "logged_errors_or_warnings=$errs"
[ "$errs" = "0" ] || { cat /home/*/.claude-pace-maker/*.log; rc=1; }
python3 - "$ITER" <<'PY' || rc=1
import sqlite3, sys
n = int(sys.argv[1])
u = sqlite3.connect("/var/lib/claude-pace-maker/usage.db")
a = dict(u.execute("select os_user, count(*) from activity_events group by os_user"))
r = sqlite3.connect("/var/lib/claude-pace-maker/session_registry.db")
s = dict(r.execute("select os_user, count(*) from sessions group by os_user"))
print("activity_rows_by_user", a, "session_rows_by_user", s)
tables = {x[0] for x in u.execute("select name from sqlite_master")}
assert "secrets" not in tables, "secrets leaked into the shared usage.db"
assert a == {"alice": n, "bob": n}, a
assert s == {"alice": n, "bob": n}, s
print("rows_ok")
PY
for u in alice bob; do
  m=$(stat -c %a "/home/$u/.claude-pace-maker/secrets.db")
  echo "$u secrets.db mode=$m"; [ "$m" = "600" ] || rc=1
done
exit $rc
