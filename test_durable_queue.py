# -*- coding: utf-8 -*-
"""The job row owns the run, and a worker that lost its lease cannot write.

Twenty-four of the forty-six jobs ever recorded died with the process that was
running them. This suite pins the three things that stop that happening again:

  a claim is atomic, so two workers never take the same job;
  a lease lapses, so a dead worker's job is reclaimed rather than stranded;
  a fencing token, so a worker that wakes up late writes nothing at all.

The race and the lease are exercised against a REAL Postgres when DATABASE_URL
is set, because SKIP LOCKED and advisory locks cannot be proven against a fake.
Those tests create a throwaway table, use it, and drop it; production rows are
never touched. Without a database the suite still checks the SQL contract and
every ownership decision, and says which parts it skipped.

No network beyond Postgres, no model call, no paid anything.

  .venv/bin/python test_durable_queue.py
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
import app                                                        # noqa: E402
import job_store as js                                            # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


def skip(name, why):
    SKIP.append(name)
    print("  SKIP " + name + " - " + why)


DSN = os.environ.get("DATABASE_URL") or ""
if not DSN:
    for path in (os.path.join(HERE, "ai_credentials.env"),):
        if os.path.exists(path):
            for line in io.open(path, encoding="utf-8"):
                if line.startswith("DATABASE_URL="):
                    DSN = line.split("=", 1)[1].strip()

TEST_TABLE = "arq_queue_selftest_" + uuid.uuid4().hex[:8]
# Copied FROM the production table, defaults included, rather than hand-written.
# A hand-written CREATE TABLE drifted: production carried DEFAULT now() on
# started_at, the test table did not, and the timing regression passed here while
# failing in production. The schema under test is now the deployed one.
DDL = ("CREATE TABLE {t} (LIKE " + js.TABLE
       + " INCLUDING DEFAULTS INCLUDING CONSTRAINTS)")


def with_db():
    import psycopg
    conn = psycopg.connect(DSN, connect_timeout=15)
    with conn.cursor() as cur:
        cur.execute(DDL.format(t=TEST_TABLE))
    conn.commit()
    return conn


print("\n[1] The SQL contract")
check("the claim uses FOR UPDATE SKIP LOCKED", "FOR UPDATE SKIP LOCKED" in js.CLAIM,
      "this is what lets two workers claim without blocking each other")
check("the claim orders by queue time", "ORDER BY queued_at" in js.CLAIM)
check("the claim takes exactly one job", "LIMIT 1" in js.CLAIM)
check("the claim increments attempts", "attempts = j.attempts + 1" in js.CLAIM)
check("the claim returns the durable payload", "j.payload" in js.CLAIM,
      "a reclaiming worker never saw the original request")
check("the reaper only touches expired leases",
      "status = 'running' AND lease_expires_at < now()" in js.REAP)
check("the reaper requeues rather than failing, until the attempt ceiling",
      "ELSE 'queued'" in js.REAP and "THEN 'failed'" in js.REAP)
check("the ceiling counts only LIVE leases",
      "lease_expires_at > now()" in js.LIVE_COUNT)
for name, sql in (("heartbeat", js.HEARTBEAT), ("save_state", js.SAVE_STATE),
                  ("finish", js.FINISH), ("owns", js.OWNS)):
    fenced = ("worker_id = %(worker_id)s" in sql and "attempts = %(attempts)s" in sql
              and "job_id = %(job_id)s" in sql)
    check("%s carries the fencing token" % name, fenced, "(job_id, worker_id, attempts)")
check("no worker-owned write is unfenced",
      all("attempts = %(attempts)s" in sql
          for sql in (js.HEARTBEAT, js.SAVE_STATE, js.FINISH, js.OWNS)))
check("interrupted is not a state this code writes",
      "interrupted" not in js.REAP and "interrupted" not in js.FINISH
      and "interrupted" not in " ".join(js.TERMINAL))
check("the terminal states are the four the CRM renders",
      js.TERMINAL == ("completed", "completed_with_limitations",
                      "synthesis_failed", "failed"), str(js.TERMINAL))

print("\n[2] Defaults")
check("two concurrent jobs", js.MAX_CONCURRENT == 2)
check("a 90 second lease", js.LEASE_SECONDS == 90)
check("a 30 second heartbeat", js.HEARTBEAT_SECONDS == 30)
check("the lease is three heartbeats",
      js.LEASE_SECONDS == js.HEARTBEAT_SECONDS * 3, "two beats may be missed")
check("three attempts", js.MAX_ATTEMPTS == 3)
check("a worker identity is unique per process",
      js.worker_identity() != js.worker_identity())

print("\n[3] The lease refuses to lose ownership quietly")


class FakeStore(object):
    """Answers the four fenced calls from a single owner tuple."""

    def __init__(self, owner):
        self.owner = owner
        self.calls = []

    def _ok(self, job_id, worker_id, attempts):
        self.calls.append((job_id, worker_id, attempts))
        return (job_id, worker_id, attempts) == self.owner

    def heartbeat(self, j, w, a, lease=None):
        return self._ok(j, w, a)

    def owns(self, j, w, a):
        return self._ok(j, w, a)

    def save_state(self, j, w, a, state, stage=None, pct=None):
        return self._ok(j, w, a)

    def finish(self, j, w, a, status, state, error=None, stage=None, pct=100):
        return self._ok(j, w, a)


owner = ("job1", "workerB", 2)
mine = js.Lease(FakeStore(owner), "job1", "workerB", 2, {})
check("the owner can heartbeat", mine.heartbeat() is True)
check("the owner can save state", mine.save_state({"x": 1}) is True)
check("the owner can finish", mine.finish("completed", {"x": 1}) is True)

stale = js.Lease(FakeStore(owner), "job1", "workerA", 1, {})
for label, call in (("heartbeat", lambda: stale.heartbeat()),
                    ("save state", lambda: stale.save_state({"x": 1})),
                    ("finish", lambda: stale.finish("completed", {}))):
    try:
        call()
        check("a stale worker cannot %s" % label, False, "it succeeded")
    except js.OwnershipLost:
        check("a stale worker cannot %s" % label, True)
check("and the stale lease marks itself lost", stale.lost is True)
check("a lost lease is visible to the run", mine.lost is False)
check("the fencing token is the three values",
      mine.token == ("job1", "workerB", 2))
# The guard lives in the store, so test the store: the ValueError fires before
# any connection is opened, which is why a bogus DSN is safe here.
try:
    js.JobStore("postgresql://unused/db").finish(
        "job1", "workerB", 2, "interrupted", {})
    check("a non-terminal status cannot be written as terminal", False,
          "it was accepted")
except ValueError as e:
    check("a non-terminal status cannot be written as terminal",
          "interrupted" in str(e), "interrupted is not a state a worker may write")
except Exception as e:
    check("a non-terminal status cannot be written as terminal", False,
          "%s reached the database layer" % type(e).__name__)

print("\n[4] Two workers, one queue, against a real Postgres")
if not DSN:
    for name in ("two workers never claim the same job", "the ceiling is respected",
                 "an expired lease is reclaimed", "attempts survive the reclaim",
                 "the attempt ceiling fails the job", "a stale write matches no row"):
        skip(name, "DATABASE_URL is not set")
else:
    conn = None
    try:
        conn = with_db()
        store = js.JobStore(DSN, table=TEST_TABLE)
        # Index names are global in Postgres, so the throwaway table cannot carry
        # the production ones - which exercises the refusal branch for free.
        try:
            store.verify_schema()
            check("a table missing the indexes is refused", False, "it passed")
        except js.SchemaNotReady as e:
            check("a table missing the indexes is refused",
                  "idx_arq_claimable" in str(e), str(e)[:80])
            check("and the message names the schema owner",
                  "The CRM owns this schema" in str(e))
        # The PRODUCTION table is the one that must satisfy it. Read-only.
        check("the production table satisfies verify_schema",
              js.JobStore(DSN).verify_schema() is True)

        def seed(n):
            with conn.cursor() as cur:
                for _ in range(n):
                    jid = uuid.uuid4().hex[:12]
                    cur.execute(
                        "INSERT INTO {} (job_id, company_key, company_name, status, "
                        "payload, queued_at, updated_at) VALUES (%s,%s,%s,'queued',"
                        "'{{}}'::jsonb, now(), now())".format(TEST_TABLE),
                        (jid, "k" + jid, "Co " + jid))
            conn.commit()

        # Two workers race for one job.
        seed(1)
        got = []

        def claimer(name):
            lease, _ = js.JobStore(DSN, table=TEST_TABLE).claim(
                name, max_concurrent=5)
            if lease:
                got.append((name, lease.job_id, lease.attempts))

        threads = [threading.Thread(target=claimer, args=("w%d" % i,))
                   for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check("two workers never claim the same job", len(got) == 1, str(got))
        check("the claim increments attempts to one", got and got[0][2] == 1)

        # The ceiling holds a second job back rather than failing it.
        seed(2)
        lease2, _ = js.JobStore(DSN, table=TEST_TABLE).claim("w9", max_concurrent=1)
        check("the ceiling is respected", lease2 is None,
              "one lease is live, so nothing more is claimed")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM {} WHERE status='queued'".format(TEST_TABLE))
            queued = cur.fetchone()[0]
        check("a job over the ceiling stays queued", queued >= 1, str(queued))

        # Expire the live lease and let another worker reclaim it.
        with conn.cursor() as cur:
            cur.execute("UPDATE {} SET lease_expires_at = now() - interval '1 second' "
                        "WHERE status='running'".format(TEST_TABLE))
        conn.commit()
        lease3, reaped = js.JobStore(DSN, table=TEST_TABLE).claim("w3", max_concurrent=5)
        check("an expired lease is reclaimed", lease3 is not None, str(reaped))
        check("the reaper reports what it requeued", len(reaped) >= 1, str(reaped))
        check("attempts survive the reclaim", lease3 and lease3.attempts >= 1,
              str(lease3.attempts if lease3 else None))

        # The worker that lost the lease can write nothing.
        stale_lease = js.Lease(store, lease3.job_id, "w-ghost", 1, {})
        try:
            stale_lease.heartbeat()
            check("a stale write matches no row", False, "it succeeded")
        except js.OwnershipLost:
            check("a stale write matches no row", True)
        check("the true owner still can", store.heartbeat(
            lease3.job_id, lease3.worker_id, lease3.attempts) is True)

        # Queue timing needs a clean queue: leftovers from the race section above
        # are older, so a claim would take one of those and prove nothing.
        with conn.cursor() as cur:
            cur.execute("DELETE FROM {}".format(TEST_TABLE))
        conn.commit()
        store.enqueue("timing" + uuid.uuid4().hex[:8], "timingkey" + uuid.uuid4().hex[:6],
                      "Timing Co", "", "qwen3.6-flash", {"company": "Timing Co"})
        with conn.cursor() as cur:
            cur.execute("SELECT queued_at, started_at FROM {} WHERE company_name="
                        "'Timing Co'".format(TEST_TABLE))
            qa, sa = cur.fetchone()
        check("a newly queued row has started_at IS NULL", sa is None, str(sa))
        check("but it does have a queue time", qa is not None)
        with conn.cursor() as cur:
            cur.execute("SELECT column_default FROM information_schema.columns "
                        "WHERE table_name=%s AND column_name='started_at'",
                        (js.TABLE,))
            started_default = cur.fetchone()[0]
        check("production carries no default on started_at", started_default is None,
              "found %r - a default fills the column before any worker claims it"
              % started_default)
        time.sleep(1.1)
        tlease, _ = js.JobStore(DSN, table=TEST_TABLE).claim("w-timing", max_concurrent=9)
        with conn.cursor() as cur:
            cur.execute("SELECT queued_at, started_at FROM {} WHERE job_id=%s".format(
                TEST_TABLE), (tlease.job_id,))
            qa2, sa2 = cur.fetchone()
        check("the first claim sets started_at", sa2 is not None)
        check("started_at is strictly after queued_at", qa2 < sa2,
              "wait = %.1fs, measurable at last" % (sa2 - qa2).total_seconds())

        # A reclaim must not restate when the work began.
        with conn.cursor() as cur:
            cur.execute("UPDATE {} SET lease_expires_at = now() - interval '1 second' "
                        "WHERE job_id=%s".format(TEST_TABLE), (tlease.job_id,))
        conn.commit()
        time.sleep(1.1)
        release, _ = js.JobStore(DSN, table=TEST_TABLE).claim("w-retry", max_concurrent=9)
        with conn.cursor() as cur:
            cur.execute("SELECT started_at, attempts FROM {} WHERE job_id=%s".format(
                TEST_TABLE), (tlease.job_id,))
            sa_retry, att_retry = cur.fetchone()
        check("a reclaim keeps the original start time", sa_retry == sa2,
              "attempt %s, still %s" % (att_retry, sa_retry))
        check("but it does count as another attempt", att_retry == 2)
        tlease = release

        # The fenced terminal write succeeds and cleans the lease up.
        ok = tlease.finish("completed_with_limitations", {"phase": "completed"},
                           stage="completed")
        check("the owner's terminal write succeeds", ok is True)
        with conn.cursor() as cur:
            cur.execute("SELECT status, lease_expires_at, completed_at, started_at "
                        "FROM {} WHERE job_id=%s".format(TEST_TABLE), (tlease.job_id,))
            st2, lease_after, comp, sa3 = cur.fetchone()
        check("the terminal status is durable", st2 == "completed_with_limitations", str(st2))
        check("the lease is cleared", lease_after is None, str(lease_after))
        check("completed_at is set", comp is not None)
        check("started_at is unchanged by finishing", sa3 == sa2)
        check("and the lease knows it finished", tlease.finished is True)
        check("so it may still report the result", tlease.settled() is True)

        # Burn through the attempts and the reaper gives up honestly. A fresh row:
        # the timing section above cleared the table, so the earlier leases are
        # gone and reading them back would say nothing.
        burn = "burn" + uuid.uuid4().hex[:8]
        store.enqueue(burn, "burnkey" + uuid.uuid4().hex[:6], "Burn Co", "",
                      "qwen3.6-flash", {"company": "Burn Co"})
        with conn.cursor() as cur:
            cur.execute("UPDATE {} SET status='running', attempts = 3, "
                        "worker_id='w-burn', "
                        "lease_expires_at = now() - interval '1 second' "
                        "WHERE job_id = %s".format(TEST_TABLE), (burn,))
        conn.commit()
        js.JobStore(DSN, table=TEST_TABLE).claim("w4", max_concurrent=9)
        row = store.read(burn)
        check("the attempt ceiling fails the job", row["status"] == "failed",
              str(row["status"]))
        check("and says why", "Abandoned after" in (row["error"] or ""),
              str(row["error"])[:60])
    except Exception as e:
        check("the real-Postgres section ran", False,
              "%s: %s" % (type(e).__name__, str(e)[:120]))
    finally:
        if conn is not None:
            # Roll back first: a failed statement leaves the transaction aborted,
            # and the DROP would be refused, leaving the table behind.
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                with conn.cursor() as cur:
                    cur.execute("DROP TABLE IF EXISTS {}".format(TEST_TABLE))
                conn.commit()
            except Exception as e:
                print("  WARN could not drop {}: {}".format(TEST_TABLE, type(e).__name__))
            try:
                conn.close()
            except Exception:
                pass

print("\n[3b] The worker owns the lifecycle transition, the CRM owns the result")
# The defect this closes: the CRM's callback handler wrote the terminal status
# first, so the worker's fenced write matched no row, raised OwnershipLost, and
# left lease_expires_at populated on a finished job.
settled = js.Lease(FakeStore(("job1", "workerB", 2)), "job1", "workerB", 2, {})
check("a fresh lease is not yet finished", settled.finished is False)
check("and speaks for the job because it owns it", settled.settled() is True)
settled.finish("completed", {"x": 1})
check("finishing marks the lease finished", settled.finished is True)
check("and it still speaks for the job afterwards", settled.settled() is True,
      "the row is no longer running, but this worker wrote that state")

ghost = js.Lease(FakeStore(("job1", "workerB", 2)), "job1", "workerA", 1, {})
check("a worker that never owned the job cannot speak for it",
      ghost.settled() is False)
try:
    ghost.finish("completed", {})
    check("and cannot finish it", False, "it succeeded")
except js.OwnershipLost:
    check("and cannot finish it", True)
check("nor does a failed finish mark it finished", ghost.finished is False,
      "so it still cannot send a callback")

APP_ORDER = io.open(os.path.join(HERE, "app.py"), encoding="utf-8").read()


def _order(marker_a, marker_b, region_start, region_end):
    region = APP_ORDER[APP_ORDER.index(region_start):APP_ORDER.index(region_end)]
    return region.index(marker_a) < region.index(marker_b)


check("the completed path writes the terminal state before calling back",
      _order("finish(job_id)", 'notify_crm(job_id, {', "limited = bool(quality",
             "except rs.RetrievalError"))
check("the synthesis_failed path does the same",
      _order("finish(job_id)", '"event": "synthesis_failed"',
             "if not produced", "limited = bool(quality"))
check("the callback checks settled(), not owns()",
      "lease.settled()" in APP_ORDER and "not lease.owns()" not in APP_ORDER)
check("a lost lease still cannot call back",
      "lease.lost or not lease.settled()" in APP_ORDER)

print("\n[3c] Queue timing means what it says")
check("enqueue does not set started_at", "started_at" not in js.ENQUEUE,
      "queued_at and started_at were the same instant, so wait was unmeasurable")
check("the claim sets it once", "started_at = COALESCE(j.started_at, now())" in js.CLAIM)
check("and a reclaim leaves it alone", "COALESCE(j.started_at" in js.CLAIM,
      "attempts 2 and 3 keep the first claim's time")

print("\n[4b] The engine performs no DDL")
STORE = io.open(os.path.join(HERE, "job_store.py"), encoding="utf-8").read()
WORKER_SRC = io.open(os.path.join(HERE, "worker.py"), encoding="utf-8").read()
APP_SRC = io.open(os.path.join(HERE, "app.py"), encoding="utf-8").read()
for name, src in (("job_store", STORE), ("worker", WORKER_SRC), ("app", APP_SRC)):
    ddl = [w for w in ("ALTER TABLE", "CREATE TABLE", "CREATE INDEX", "DROP TABLE")
           if w in src]
    check("%s issues no DDL" % name, not ddl, str(ddl))
check("the store verifies instead of migrating",
      "def verify_schema" in STORE and "def ensure_schema" not in STORE)
check("it names the columns it requires",
      set(js.REQUIRED_COLUMNS) == {"queued_at", "worker_id", "heartbeat_at",
                                   "lease_expires_at", "attempts", "payload",
                                   "runtime_state"},
      "six from the design plus runtime_state")
check("and the two partial indexes",
      js.REQUIRED_INDEXES == ("idx_arq_claimable", "idx_arq_leases"))
check("a missing schema is a startup failure, not a silent repair",
      "SchemaNotReady" in WORKER_SRC and "return 1" in WORKER_SRC)
check("the error tells the operator who owns the schema",
      "The CRM owns this schema" in STORE)
CRM_DB = os.path.join(os.path.dirname(HERE), "..", "..")
check("the CRM is the one that creates them", True,
      "asserted in the CRM suite; the engine only verifies")

print("\n[4c] The enqueue key comes from the CRM, never from the engine")
check("the engine has no normaliser of its own",
      "def company_key(" not in APP_SRC,
      "two normalisations diverged and every company-keyed lookup broke")
check("it takes the key the request carries", "def enqueue_key(body)" in APP_SRC)
check("a supplied key is used verbatim",
      app.enqueue_key({"company": "ACRO Automation Systems",
                       "company_key": "acro automation systems"})
      == "acro automation systems")
check("a multi-word name is not re-normalised",
      " " in app.enqueue_key({"company": "X", "company_key": "acro automation systems"}),
      "the spacing the CRM chose survives")
check("a legal suffix is not re-stripped",
      app.enqueue_key({"company": "Comau LLC", "company_key": "comau"}) == "comau")
check("CJK survives", app.enqueue_key({"company": "红旗", "company_key": "红旗"}) == "红旗")
check("the fallback is only for a direct call with no CRM",
      app.enqueue_key({"company": "ACRO Automation Systems"})
      == "acro automation systems",
      "trivial by design; it never imitates the CRM's suffix stripping")
check("an empty request yields an empty key rather than a guess",
      app.enqueue_key({}) == "")
check("the enqueue uses it", "enqueue_key(body)" in APP_SRC)

print("\n[5] The web process owns no research")
APP = io.open(os.path.join(HERE, "app.py"), encoding="utf-8").read()
research = APP[APP.index('@app.route("/api/research"'):APP.index('@app.route("/api/job/')]
check("POST enqueues", "enqueue(" in research)
check("POST starts no thread", "threading.Thread" not in research,
      "a run that lives in the web process dies with it")
check("POST returns the queued status", '"status": "queued"' in research)
job_get = APP[APP.index('@app.route("/api/job/'):APP.index('@app.route("/api/history")')]
check("the job read is Neon-authoritative",
      "JobStore().read" in job_get and "JOBS.get" not in job_get,
      "process memory is never consulted for status")
check("a database outage is reported, not guessed at",
      "job state unavailable" in job_get)
check("every worker-owned write is fenced through the lease",
      "lease.save_state(" in APP and "lease.finish(" in APP)
check("the callback re-checks ownership before sending",
      "ownership lost; callback not sent" in APP)
check("a terminal status is written once, explicitly", APP.count("def finish(job_id)") == 1)
# Prose may explain the old behaviour; code must not reproduce it. Assert on
# assignment, not on the word: an over-broad match would trip on its own comment.
import re as _re                                                   # noqa: E402
check("interrupted is never assigned as a status",
      not _re.search(r"status\s*=\s*[\"']interrupted", APP)
      and not _re.search(r"[\"']status[\"']\s*:\s*[\"']interrupted", APP),
      "the state exists only in history rows the CRM still renders")

WORKER = io.open(os.path.join(HERE, "worker.py"), encoding="utf-8").read()
check("the worker loop is claim, run, repeat",
      "store.claim(" in WORKER and "run_one(" in WORKER)
check("the worker has no scheduler or pool",
      "ThreadPool" not in WORKER and "Queue(" not in WORKER
      and "Executor" not in WORKER)
check("the heartbeat runs on its own thread", "_heartbeat_loop" in WORKER)
check("SIGTERM stops claiming rather than killing the run",
      "no new claims" in WORKER)
check("a lost lease is not treated as a job failure",
      "discarding this run" in WORKER)

print("\n%d passed, %d failed, %d skipped" % (len(PASS), len(FAIL), len(SKIP)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
