# -*- coding: utf-8 -*-
"""One research worker. Claim a job, run it, repeat.

    reap expired leases
    claim one job
    run it
    heartbeat while running
    write and call back only while ownership is valid
    repeat

There is no scheduler, no dispatcher, no thread pool and no queue abstraction.
One worker runs one job: it is either idle or busy, so there is nothing to
schedule. Concurrency comes from running two of these processes.

The research pipeline itself is untouched. This module decides WHEN a run
happens and WHO owns it; app.worker decides what a run does, exactly as before.

    python worker.py
"""
import os
import signal
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app                                                        # noqa: E402
import job_store as js                                            # noqa: E402
import research_service as rs                                     # noqa: E402

STOPPING = threading.Event()

# How often the maintenance loop returns expired leases to the queue. Short
# enough that recovery is prompt, long enough to be free: one UPDATE that
# usually matches no rows.
REAP_SECONDS = int(os.environ.get("RESEARCH_REAP_SECONDS", "45"))


def log(message):
    sys.stdout.write("[worker] {}\n".format(message))
    sys.stdout.flush()


def _heartbeat_loop(lease, stop):
    """Extend the lease while the run proceeds.

    Deliberately separate from progress: a job stuck on a slow page fetch is
    alive, and treating silence as death would reclaim healthy runs. If the
    heartbeat itself fails the lease is gone, so the run is told to stop rather
    than continue writing into a job somebody else now owns.
    """
    while not stop.wait(js.HEARTBEAT_SECONDS):
        try:
            lease.heartbeat()
        except js.OwnershipLost:
            log("lease lost for {} - signalling the run to stop".format(lease.job_id))
            app.abandon(lease.job_id)
            return
        except Exception as e:
            log("heartbeat error ({}) - will retry".format(type(e).__name__))


def _reaper_loop(store, stop):
    """Return expired leases to the queue, whatever this worker is doing.

    Reaping used to happen only inside claim(), so a worker busy with a long run
    could not recover a job that a dead process had abandoned until its own job
    finished. That is exactly how a job sat orphaned for eight minutes while
    another ran, with the CRM's 25-minute net waiting to terminalise it.

    This loop does no research and claims nothing. It requeues, or fails a job
    that has exhausted its attempts, using the same statement and the same
    advisory lock as before.
    """
    while not stop.wait(REAP_SECONDS):
        try:
            for job_id, attempts, status in store.reap():
                log("reaped {} after a lapsed lease -> {} (attempt {})".format(
                    job_id, status, attempts))
        except Exception as e:
            # Never fatal: the next tick tries again, and claim() still reaps.
            log("reaper error ({}) - will retry".format(type(e).__name__))


def run_one(lease):
    """Hand the claimed job to the existing pipeline, under the fencing token."""
    log("claimed {} ({}) attempt {}".format(lease.job_id, lease.company, lease.attempts))
    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat_loop, args=(lease, stop), daemon=True)
    beat.start()
    started = time.time()
    payload = lease.payload or {}
    models = payload.get("models") or [lease.model]
    try:
        app.adopt(lease)
        app.worker(lease.job_id, lease.company, lease.website or "", models,
                   bool(payload.get("use_cache")), bool(payload.get("force")),
                   payload.get("known_contacts") or [])
    except js.OwnershipLost:
        # Another worker owns this job. Everything this one wrote was refused,
        # which is the design working; say so and take the next job.
        log("ownership lost during {} - discarding this run".format(lease.job_id))
    except Exception:
        log("run failed for {}:\n{}".format(lease.job_id, traceback.format_exc()))
        try:
            lease.finish("failed", app.runtime_state(lease.job_id),
                         error="Worker error: see logs.", stage="error")
        except js.OwnershipLost:
            pass
    finally:
        stop.set()
        app.release(lease.job_id)
        log("finished {} in {:.1f}s".format(lease.job_id, time.time() - started))


def main():
    worker_id = js.worker_identity()
    store = js.JobStore()
    try:
        store.verify_schema()
    except js.SchemaNotReady as e:
        # The CRM owns this schema. Fail loudly at startup rather than issuing
        # DDL of our own or discovering the gap halfway through a run.
        log("SCHEMA NOT READY: {}".format(e))
        return 1
    log("started as {} | lease {}s | heartbeat {}s | ceiling {}".format(
        worker_id, js.LEASE_SECONDS, js.HEARTBEAT_SECONDS, js.MAX_CONCURRENT))

    def on_term(_signum, _frame):
        # Stop claiming immediately. A job already running keeps its lease and
        # finishes; if the platform kills us first the lease lapses and another
        # worker takes it, which is the whole point of the lease.
        log("SIGTERM: no new claims")
        STOPPING.set()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    # Independent of the claim cycle, and stopped by the same event, so SIGTERM
    # ends it cleanly rather than leaving it to be killed mid-statement.
    reaper = threading.Thread(target=_reaper_loop, args=(store, STOPPING),
                              daemon=True, name="reaper")
    reaper.start()
    log("reaper started: every {}s, independent of the claim loop".format(REAP_SECONDS))

    while not STOPPING.is_set():
        try:
            lease, reaped = store.claim(worker_id)
            for job_id, attempts, status in reaped:
                log("reaped {} after a lapsed lease -> {} (attempt {})".format(
                    job_id, status, attempts))
            if lease is None:
                STOPPING.wait(js.CLAIM_POLL_SECONDS)
                continue
            run_one(lease)
        except KeyboardInterrupt:
            break
        except Exception:
            log("claim loop error:\n{}".format(traceback.format_exc()))
            STOPPING.wait(js.CLAIM_POLL_SECONDS)
    reaper.join(timeout=5)
    log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
