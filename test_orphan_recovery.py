# -*- coding: utf-8 -*-
"""A job orphaned by a dead worker must not wait for the next one to finish.

The incident, exactly: a worker process died holding IMA. Its replacement
started while IMA's lease was still VALID, so the reaper - which only ever ran
inside claim() - found nothing and the replacement took the next job instead.
IMA's lease then expired with the replacement six minutes deep in another run,
and nothing could reap it until that run ended. Meanwhile the CRM's 25-minute
net was waiting to mark a still-recoverable job `interrupted`.

The fix is a maintenance loop that reaps on its own schedule. This suite
reproduces the sequence step for step against a REAL Postgres, because the
advisory lock, SKIP LOCKED and the guarded UPDATE cannot be proven against a
fake. It creates a throwaway table copied from the production one, uses it, and
drops it; production rows are never touched.

No model call, no research, no paid anything.

  DATABASE_URL=... .venv/bin/python test_orphan_recovery.py
"""
import io
import os
import re
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import job_store as js                                            # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


def skip(name, why):
    SKIP.append(name)
    print("  SKIP " + name + " - " + why)


DSN = os.environ.get("DATABASE_URL") or ""
T = "arq_orphan_selftest_" + uuid.uuid4().hex[:8]
DDL = ("CREATE TABLE {t} (LIKE " + js.TABLE
       + " INCLUDING DEFAULTS INCLUDING CONSTRAINTS)")

# The CRM's sweeper, as it stands in db.js. Mirrored here so the test can prove
# it does NOT reach a job the worker reaper has already rescued.
CRM_SWEEP = """
UPDATE {t}
   SET status='interrupted', completed_at=NOW(), updated_at=NOW(),
       error=COALESCE(error, 'Interrupted: no worker has held this run for 25 minutes.')
 WHERE status = 'running'
   AND (lease_expires_at IS NULL OR lease_expires_at < NOW() - INTERVAL '25 minutes')
   AND updated_at < NOW() - INTERVAL '25 minutes'
RETURNING job_id
"""


print("\n[1] The reaper is reachable without claiming anything")
check("JobStore.reap exists", hasattr(js.JobStore, "reap"))
src = io.open(os.path.join(HERE, "job_store.py"), encoding="utf-8").read()
reap_src = src[src.index("    def reap(self"):src.index("    def heartbeat(self")]
check("it runs the same REAP statement", "self._sql(REAP)" in reap_src)
check("it takes the same advisory lock", "arq_claim" in reap_src)
check("it does NOT claim", "CLAIM" not in reap_src.replace("REAP", ""))
check("it respects the attempt ceiling", "max_attempts" in reap_src)
check("only an EXPIRED lease is touched",
      "status = 'running' AND lease_expires_at < now()" in js.REAP,
      "a live lease must never be reclaimed")
check("the ceiling fails rather than requeues",
      "WHEN attempts >= %(max_attempts)s THEN 'failed'" in js.REAP)
check("ownership is revoked, not stolen", "worker_id = NULL" in js.REAP)

wsrc = io.open(os.path.join(HERE, "worker.py"), encoding="utf-8").read()
print("\n[2] The maintenance loop")
check("the worker runs a reaper loop", "_reaper_loop" in wsrc)
check("it is started as its own thread",
      re.search(r"threading\.Thread\(target=_reaper_loop", wsrc) is not None)
check("on a modest interval",
      30 <= int(re.search(r'RESEARCH_REAP_SECONDS", "(\d+)"', wsrc).group(1)) <= 60,
      "every 45s by default")
check("it stops on the same event as the worker",
      "while not stop.wait(REAP_SECONDS)" in wsrc,
      "SIGTERM sets STOPPING and the wait returns immediately")
check("shutdown waits for it", "reaper.join(timeout=5)" in wsrc)
check("it performs no research",
      "app.worker" not in wsrc[wsrc.index("def _reaper_loop"):wsrc.index("def run_one")])
check("a reaper error is never fatal",
      "will retry" in wsrc[wsrc.index("def _reaper_loop"):wsrc.index("def run_one")])


def db():
    import psycopg
    conn = psycopg.connect(DSN, connect_timeout=15)
    with conn.cursor() as cur:
        cur.execute(DDL.format(t=T))
    conn.commit()
    return conn


def seed(conn, job_id, company):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO {t} (job_id, company_key, company_name, model, status, "
            "stage, progress_percent, queued_at, attempts, updated_at) "
            "VALUES (%s,%s,%s,'test','queued','queued',0, now(), 0, now())".format(t=T),
            (job_id, company.lower(), company))
    conn.commit()


def row(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("SELECT status, attempts, worker_id, lease_expires_at, started_at "
                    "FROM {t} WHERE job_id=%s".format(t=T), (job_id,))
        r = cur.fetchone()
    return dict(zip(("status", "attempts", "worker_id", "lease", "started_at"), r))


def expire(conn, job_id):
    """What a dead process looks like from the outside: the lease runs out."""
    with conn.cursor() as cur:
        cur.execute("UPDATE {t} SET lease_expires_at = now() - INTERVAL '1 second' "
                    "WHERE job_id=%s".format(t=T), (job_id,))
    conn.commit()


if not DSN:
    for n in ["the incident sequence", "a live lease is untouched",
              "the attempt ceiling", "no double requeue", "the CRM net does not race"]:
        skip(n, "DATABASE_URL is not set")
else:
    conn = db()
    store = js.JobStore(dsn=DSN, table=T)
    try:
        print("\n[3] The incident, step for step")
        seed(conn, "A", "IMA")
        seed(conn, "B", "Intercable")

        lease_a, reaped = store.claim("worker-A")
        check("1. worker A claims job A", lease_a is not None and lease_a.job_id == "A",
              lease_a.job_id if lease_a else "none")
        check("   with attempt 1", row(conn, "A")["attempts"] == 1)

        # 2. Process A dies. Nothing writes; the lease is simply not renewed.
        #    3. The replacement starts while that lease is STILL VALID.
        before = row(conn, "A")
        lease_b, reaped_b = store.claim("worker-B")
        check("2-3. the replacement starts while A's lease is still valid",
              row(conn, "A")["status"] == "running" and not reaped_b,
              "the old reaper correctly found nothing to do")
        check("4. so it claims job B instead",
              lease_b is not None and lease_b.job_id == "B", lease_b.job_id if lease_b else "none")

        # 5. A's lease expires while B is still running.
        expire(conn, "A")
        check("5. A's lease has expired, B is still running",
              row(conn, "A")["status"] == "running" and row(conn, "B")["status"] == "running")

        # 6. Maintenance runs. B is NOT finished, and is not asked to be.
        got = store.reap()
        check("6. maintenance requeues A without B finishing",
              [g[0] for g in got] == ["A"], str(got))
        a = row(conn, "A")
        check("   A is queued again", a["status"] == "queued", a["status"])
        check("   its attempt count is preserved for the retry", a["attempts"] == 1,
              str(a["attempts"]))
        check("   ownership was revoked", a["worker_id"] is None)
        check("   and its lease cleared", a["lease"] is None)
        check("   B was not touched at all",
              row(conn, "B")["status"] == "running"
              and row(conn, "B")["worker_id"] == "worker-B")

        # 7. The CRM net must not reach a job that is no longer running.
        with conn.cursor() as cur:
            cur.execute(CRM_SWEEP.format(t=T))
            swept = [r[0] for r in cur.fetchall()]
        conn.commit()
        check("7. CRM cleanup does not mark A interrupted", "A" not in swept, str(swept))
        check("   because A is queued, and the net only reads running rows",
              row(conn, "A")["status"] == "queued")

        # 8. A is claimable again and resumes on the next attempt.
        lease_a2, _ = store.claim("worker-B")
        check("8. A is claimable again", lease_a2 is not None and lease_a2.job_id == "A",
              lease_a2.job_id if lease_a2 else "none")
        check("   on attempt 2", row(conn, "A")["attempts"] == 2, str(row(conn, "A")["attempts"]))

        print("\n[4] What the reaper must NOT do")
        check("a live lease is untouched",
              [g[0] for g in store.reap()] == [],
              "B and the requeued A both hold valid leases")
        check("B is still running after that reap", row(conn, "B")["status"] == "running")

        expire(conn, "A")
        store.reap()
        check("a second reap cannot requeue the same job twice",
              [g[0] for g in store.reap()] == [], "the first one already moved it")

        # The ceiling: a job that has burned its attempts is failed, not looped.
        with conn.cursor() as cur:
            cur.execute("UPDATE {t} SET status='running', attempts=3, "
                        "lease_expires_at=now() - INTERVAL '1 second' "
                        "WHERE job_id='A'".format(t=T))
        conn.commit()
        got = store.reap(max_attempts=3)
        check("at the attempt ceiling the job is failed, not requeued",
              row(conn, "A")["status"] == "failed", row(conn, "A")["status"])
        check("and it is not left claimable", row(conn, "A")["status"] != "queued")

        print("\n[5] The old owner cannot write after being reaped")
        with conn.cursor() as cur:
            cur.execute("UPDATE {t} SET status='running', attempts=1, worker_id='worker-A', "
                        "lease_expires_at=now() - INTERVAL '1 second' "
                        "WHERE job_id='A'".format(t=T))
        conn.commit()
        store.reap()
        check("the reaped job is queued", row(conn, "A")["status"] == "queued")
        check("worker A's heartbeat is refused",
              store.heartbeat("A", "worker-A", 1) is False,
              "the fencing token no longer matches")
        check("worker A no longer owns it", store.owns("A", "worker-A", 1) is False)

        print("\n[6] The loop stops cleanly")
        import worker as w
        w.REAP_SECONDS = 0.05
        stop = threading.Event()
        t = threading.Thread(target=w._reaper_loop, args=(store, stop), daemon=True)
        t.start()
        time.sleep(0.3)
        check("the loop is running", t.is_alive())
        stop.set()
        t.join(timeout=3)
        check("setting the stop event ends it", not t.is_alive(),
              "SIGTERM sets exactly this event")
    finally:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS {t}".format(t=T))
        conn.commit()
        conn.close()
        print("\n  (throwaway table dropped)")

print("\n%d passed, %d failed, %d skipped" % (len(PASS), len(FAIL), len(SKIP)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
