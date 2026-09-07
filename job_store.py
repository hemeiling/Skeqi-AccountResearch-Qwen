# -*- coding: utf-8 -*-
"""The Neon job row is the source of truth. This module is the only thing that
writes it.

A research run used to live in one Python process's memory. Twenty-four of the
forty-six jobs ever recorded died with that process. Here the row in Postgres
owns the run: a worker CLAIMS it, holds a LEASE, and every write it makes is
conditional on still owning that claim.

    (job_id, worker_id, attempts)

is the fencing token. It appears in the WHERE clause of every worker-owned
write, so a worker whose lease expired while it was stalled cannot heartbeat,
cannot record progress, cannot send a callback and cannot write a result. Its
statements match zero rows and it stops. That is the whole of the protection
against two workers finishing the same job.

No queue framework. Postgres gives the two guarantees this needs: an atomic
claim under concurrency (FOR UPDATE SKIP LOCKED) and durability across a crash.

The table name is a parameter only so tests can race two real workers against a
throwaway table; production always uses the default.
"""
import json
import os
import socket
import uuid

TABLE = "account_research_qwen_jobs"

# Lease and cadence. A lease is three heartbeats, so two may be missed before
# another worker is entitled to the job.
LEASE_SECONDS = int(os.environ.get("RESEARCH_LEASE_SECONDS", "90"))
HEARTBEAT_SECONDS = int(os.environ.get("RESEARCH_HEARTBEAT_SECONDS", "30"))
MAX_ATTEMPTS = int(os.environ.get("RESEARCH_MAX_ATTEMPTS", "3"))
CLAIM_POLL_SECONDS = int(os.environ.get("RESEARCH_CLAIM_POLL_SECONDS", "5"))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_RESEARCH_JOBS", "2"))

# Live states, and the states a job can end in. `interrupted` is deliberately
# absent: it named a run nobody could account for, and a lapsed lease now says
# that better. Historical rows keep it.
LIVE = ("queued", "running")
TERMINAL = ("completed", "completed_with_limitations", "synthesis_failed", "failed")


class OwnershipLost(Exception):
    """Raised when a worker discovers another worker owns the job it is running.

    Not an error in the job: an error in this worker's belief about it. The only
    correct response is to stop touching the job.
    """


def worker_identity():
    """Stable enough to read in a log, unique enough to fence with."""
    return "{}:{}".format(socket.gethostname()[:40], uuid.uuid4().hex[:8])


# ---------------------------------------------------------------------------
# Schema is the CRM's. This module only checks it is there.
#
# One migration owner, and it is the process that already has one: the CRM runs
# initDb at boot and creates this table. A worker that also issued DDL would be
# a second owner racing the first, so it verifies and refuses instead.
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = ("queued_at", "worker_id", "heartbeat_at", "lease_expires_at",
                    "attempts", "payload", "runtime_state")
REQUIRED_INDEXES = ("idx_arq_claimable", "idx_arq_leases")

CHECK_COLUMNS = """
SELECT column_name FROM information_schema.columns
 WHERE table_name = %(table)s AND column_name = ANY(%(cols)s)
"""

CHECK_INDEXES = """
SELECT indexname FROM pg_indexes
 WHERE tablename = %(table)s AND indexname = ANY(%(idx)s)
"""


class SchemaNotReady(Exception):
    """The queue columns or indexes are missing.

    Not something a worker may fix. The CRM owns this schema; a worker that
    started before the CRM's migration ran should say so and exit, so the cause
    is legible instead of arriving later as a confusing SQL error mid-run.
    """


# Reap first, then claim. Both live in one transaction with an advisory lock so
# the concurrency count cannot be read by two workers that then both act on it.
REAP = """
UPDATE {t}
   SET status = CASE WHEN attempts >= %(max_attempts)s THEN 'failed' ELSE 'queued' END,
       error = CASE WHEN attempts >= %(max_attempts)s
                    THEN 'Abandoned after ' || attempts || ' attempts; the run did '
                         || 'not survive its workers.'
                    ELSE error END,
       completed_at = CASE WHEN attempts >= %(max_attempts)s THEN now() ELSE NULL END,
       worker_id = NULL,
       lease_expires_at = NULL,
       updated_at = now()
 WHERE status = 'running' AND lease_expires_at < now()
RETURNING job_id, attempts, status
"""

LIVE_COUNT = """
SELECT count(*) FROM {t}
 WHERE status = 'running' AND lease_expires_at > now()
"""

CLAIM = """
WITH claimable AS (
  SELECT job_id FROM {t}
   WHERE status = 'queued'
   ORDER BY queued_at
   LIMIT 1
   FOR UPDATE SKIP LOCKED
)
UPDATE {t} j
   SET status = 'running',
       worker_id = %(worker_id)s,
       attempts = j.attempts + 1,
       started_at = COALESCE(j.started_at, now()),   -- first claim only
       heartbeat_at = now(),
       lease_expires_at = now() + (%(lease)s || ' seconds')::interval,
       updated_at = now()
  FROM claimable c
 WHERE j.job_id = c.job_id
RETURNING j.job_id, j.attempts, j.payload, j.company_name, j.website, j.model
"""

# Every worker-owned write carries the fencing token. Rowcount 0 means the lease
# was lost and another worker owns the job now.
HEARTBEAT = """
UPDATE {t}
   SET heartbeat_at = now(),
       lease_expires_at = now() + (%(lease)s || ' seconds')::interval,
       updated_at = now()
 WHERE job_id = %(job_id)s AND worker_id = %(worker_id)s
   AND attempts = %(attempts)s AND status = 'running'
"""

SAVE_STATE = """
UPDATE {t}
   SET stage = COALESCE(%(stage)s, stage),
       progress_percent = COALESCE(%(pct)s, progress_percent),
       runtime_state = %(runtime_state)s,
       updated_at = now()
 WHERE job_id = %(job_id)s AND worker_id = %(worker_id)s
   AND attempts = %(attempts)s AND status = 'running'
"""

FINISH = """
UPDATE {t}
   SET status = %(status)s,
       stage = COALESCE(%(stage)s, stage),
       progress_percent = %(pct)s,
       error = %(error)s,
       runtime_state = %(runtime_state)s,
       lease_expires_at = NULL,
       completed_at = now(),
       updated_at = now()
 WHERE job_id = %(job_id)s AND worker_id = %(worker_id)s
   AND attempts = %(attempts)s AND status = 'running'
"""

OWNS = """
SELECT 1 FROM {t}
 WHERE job_id = %(job_id)s AND worker_id = %(worker_id)s
   AND attempts = %(attempts)s AND status = 'running'
"""

# started_at is deliberately absent: it is when a WORKER first picked the job
# up, not when the request arrived. Setting it here made queue wait unmeasurable
# because queued_at and started_at were the same instant.
ENQUEUE = """
INSERT INTO {t} (job_id, company_key, company_name, website, model, job_type,
                 status, stage, progress_percent, payload, queued_at, updated_at)
VALUES (%(job_id)s, %(company_key)s, %(company_name)s, %(website)s, %(model)s,
        %(job_type)s, 'queued', 'queued', 0, %(payload)s, now(), now())
RETURNING job_id
"""

READ = """
SELECT job_id, status, stage, progress_percent, error, worker_id, attempts,
       runtime_state, payload, company_name, website, model
  FROM {t} WHERE job_id = %(job_id)s
"""


class JobStore(object):
    """Thin wrapper over psycopg. Opens a connection per operation: the volume
    is a handful of statements per minute per worker, and a pool would be more
    machinery than the traffic justifies."""

    def __init__(self, dsn=None, table=TABLE):
        self.dsn = dsn or os.environ.get("DATABASE_URL")
        self.table = table
        if not self.dsn:
            raise ValueError("DATABASE_URL is required for durable job state")

    # -- connection ------------------------------------------------------
    def _connect(self):
        import psycopg
        return psycopg.connect(self.dsn, connect_timeout=15)

    def _sql(self, text):
        return text.format(t=self.table)

    def _run(self, text, params=None, fetch=None):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(self._sql(text), params or {})
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
                return cur.rowcount

    # -- schema ----------------------------------------------------------
    def verify_schema(self):
        """Check, never create. Raises SchemaNotReady naming what is missing."""
        cols = {r[0] for r in self._run(
            CHECK_COLUMNS, {"table": self.table, "cols": list(REQUIRED_COLUMNS)},
            fetch="all") or []}
        idx = {r[0] for r in self._run(
            CHECK_INDEXES, {"table": self.table, "idx": list(REQUIRED_INDEXES)},
            fetch="all") or []}
        missing_cols = [c for c in REQUIRED_COLUMNS if c not in cols]
        missing_idx = [i for i in REQUIRED_INDEXES if i not in idx]
        if missing_cols or missing_idx:
            raise SchemaNotReady(
                "{} is missing columns {} and indexes {}. The CRM owns this "
                "schema: start the CRM so its migration runs, then start the "
                "worker.".format(self.table, missing_cols or "none",
                                 missing_idx or "none"))
        return True

    # -- producer side ---------------------------------------------------
    def enqueue(self, job_id, company_key, company_name, website, model,
                payload, job_type="single"):
        """The API's whole responsibility. Returns the job id, or raises on a
        duplicate, which the unique partial index makes unambiguous."""
        return self._run(ENQUEUE, {
            "job_id": job_id, "company_key": company_key,
            "company_name": company_name, "website": website, "model": model,
            "job_type": job_type, "payload": json.dumps(payload),
        }, fetch="one")

    def read(self, job_id):
        row = self._run(READ, {"job_id": job_id}, fetch="one")
        if not row:
            return None
        keys = ("job_id", "status", "stage", "progress_percent", "error",
                "worker_id", "attempts", "runtime_state", "payload",
                "company_name", "website", "model")
        return dict(zip(keys, row))

    # -- worker side -----------------------------------------------------
    def claim(self, worker_id, lease=None, max_attempts=None, max_concurrent=None):
        """Reap, check the ceiling, take one job. All under one advisory lock.

        Returns a Lease, or None when there is nothing to do or the ceiling is
        reached. A job over the ceiling stays queued; it is never failed.
        """
        lease = lease or LEASE_SECONDS
        max_attempts = MAX_ATTEMPTS if max_attempts is None else max_attempts
        max_concurrent = MAX_CONCURRENT if max_concurrent is None else max_concurrent
        reaped = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                # Serialise claims. Held for the milliseconds this transaction
                # lasts; released on COMMIT or if this process dies.
                cur.execute("SELECT pg_advisory_xact_lock(hashtext('arq_claim'))")
                cur.execute(self._sql(REAP), {"max_attempts": max_attempts})
                reaped = cur.fetchall() or []
                cur.execute(self._sql(LIVE_COUNT))
                live = (cur.fetchone() or [0])[0]
                if live >= max_concurrent:
                    conn.commit()
                    return None, reaped
                cur.execute(self._sql(CLAIM),
                            {"worker_id": worker_id, "lease": lease})
                row = cur.fetchone()
                conn.commit()
        if not row:
            return None, reaped
        job_id, attempts, payload, company, website, model = row
        return Lease(self, job_id, worker_id, attempts, payload or {},
                     company, website, model), reaped

    def reap(self, max_attempts=None):
        """Return expired leases to the queue. Claims nothing, runs nothing.

        The same statement claim() runs, callable on its own so a worker that is
        busy with a long job can still recover one that a dead process left
        behind. Reaping was only ever reachable through claim(), which meant a
        worker in the middle of six minutes of research could not rescue an
        orphan until it finished - and the CRM's 25-minute net could reach the
        job first and terminalise something that was still recoverable.

        The predicate is unchanged: only `status = 'running'` with an expired
        lease. A live lease is never touched. Attempts are respected by the
        statement itself - at the ceiling the job is failed rather than requeued.

        Ownership is revoked here, not stolen: worker_id becomes NULL, so the
        old owner's fenced writes stop matching immediately.
        """
        max_attempts = MAX_ATTEMPTS if max_attempts is None else max_attempts
        with self._connect() as conn:
            with conn.cursor() as cur:
                # The same advisory lock claim() takes, so a reap and a claim
                # can never read the concurrency count at the same moment.
                cur.execute("SELECT pg_advisory_xact_lock(hashtext('arq_claim'))")
                cur.execute(self._sql(REAP), {"max_attempts": max_attempts})
                rows = cur.fetchall() or []
                conn.commit()
        return rows

    def heartbeat(self, job_id, worker_id, attempts, lease=None):
        return self._run(HEARTBEAT, {
            "job_id": job_id, "worker_id": worker_id, "attempts": attempts,
            "lease": lease or LEASE_SECONDS}) == 1

    def owns(self, job_id, worker_id, attempts):
        return self._run(OWNS, {"job_id": job_id, "worker_id": worker_id,
                                "attempts": attempts}, fetch="one") is not None

    def save_state(self, job_id, worker_id, attempts, runtime_state,
                   stage=None, pct=None):
        return self._run(SAVE_STATE, {
            "job_id": job_id, "worker_id": worker_id, "attempts": attempts,
            "stage": stage, "pct": pct,
            "runtime_state": json.dumps(runtime_state or {}),
        }) == 1

    def finish(self, job_id, worker_id, attempts, status, runtime_state,
               error=None, stage=None, pct=100):
        if status not in TERMINAL:
            raise ValueError("not a terminal status: {}".format(status))
        return self._run(FINISH, {
            "job_id": job_id, "worker_id": worker_id, "attempts": attempts,
            "status": status, "error": error, "stage": stage, "pct": pct,
            "runtime_state": json.dumps(runtime_state or {}),
        }) == 1


class Lease(object):
    """One claimed job, and the fencing token that proves it is still ours.

    Every method here refuses silently-losing behaviour: a write that matches no
    row raises OwnershipLost rather than returning False, because the caller is
    in the middle of a research run and the only safe response is to stop.
    """

    def __init__(self, store, job_id, worker_id, attempts, payload,
                 company=None, website=None, model=None):
        self.store = store
        self.job_id = job_id
        self.worker_id = worker_id
        self.attempts = attempts
        self.payload = payload if isinstance(payload, dict) else json.loads(payload or "{}")
        self.company = company or self.payload.get("company")
        self.website = website or self.payload.get("website")
        self.model = model or self.payload.get("model")
        self.lost = False
        # Set once the fenced terminal write succeeds. The row is no longer
        # 'running' after that, so owns() would say no - but this worker is
        # precisely the one entitled to report the result it just wrote.
        self.finished = False

    @property
    def token(self):
        return (self.job_id, self.worker_id, self.attempts)

    def _check(self, ok):
        if not ok:
            self.lost = True
            raise OwnershipLost(
                "job {} is no longer owned by {} at attempt {}".format(
                    self.job_id, self.worker_id, self.attempts))
        return True

    def heartbeat(self):
        return self._check(self.store.heartbeat(*self.token))

    def owns(self):
        return self.store.owns(*self.token)

    def save_state(self, runtime_state, stage=None, pct=None):
        return self._check(self.store.save_state(
            self.job_id, self.worker_id, self.attempts, runtime_state,
            stage=stage, pct=pct))

    def finish(self, status, runtime_state, error=None, stage=None, pct=100):
        ok = self._check(self.store.finish(
            self.job_id, self.worker_id, self.attempts, status, runtime_state,
            error=error, stage=stage, pct=pct))
        self.finished = True
        return ok

    def settled(self):
        """Still entitled to speak for this job: either the lease is live, or we
        are the worker that wrote its terminal state."""
        return self.finished or self.owns()
