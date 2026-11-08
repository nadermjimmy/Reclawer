"""Background jobs started from the web UI. One job runs at a time; progress is logged to the jobs table."""
import threading, time, traceback
import db

_lock = threading.Lock()
_open = set()  # jobs that hand off to background work (batched reads) and finish later


def keep_open(job_id):
    """Leave this job's status as set by the caller when its function returns."""
    _open.add(job_id)


class Log:
    def __init__(self, job_id):
        self.job_id = job_id

    def __call__(self, line):
        with db.connect() as c:
            c.execute("UPDATE jobs SET log=COALESCE(log,'')||? WHERE id=?",
                      (time.strftime("%H:%M:%S ") + str(line) + "\n", self.job_id))


def running():
    return _lock.locked()


def start(name, fn, *args):
    """Start fn(log, *args) in a thread. Returns the job id, or None if another job is running."""
    if not _lock.acquire(blocking=False):
        return None
    with db.connect() as c:
        job_id = c.execute("INSERT INTO jobs(name,status,started_at,log) VALUES(?,?,?,'')",
                           (name, "running", int(time.time()))).lastrowid

    def run():
        log = Log(job_id)
        status = "done"
        try:
            fn(log, *args)
        except Exception as e:
            status = "failed"
            log(f"ERROR: {e}")
            log(traceback.format_exc(limit=3))
        finally:
            with db.connect() as c:
                if status == "failed" or job_id not in _open:
                    c.execute("UPDATE jobs SET status=?, finished_at=? WHERE id=?",
                              (status, int(time.time()), job_id))
                else:  # handed off to background work (batched read) that marks it done
                    c.execute("UPDATE jobs SET status='waiting' WHERE id=? AND status='running'", (job_id,))
            _open.discard(job_id)
            _lock.release()

    threading.Thread(target=run, daemon=True).start()
    return job_id


def run_now(name, fn, *args):
    """Run synchronously (tests and console use)."""
    with db.connect() as c:
        job_id = c.execute("INSERT INTO jobs(name,status,started_at,log) VALUES(?,?,?,'')",
                           (name, "running", int(time.time()))).lastrowid
    fn(Log(job_id), *args)
    with db.connect() as c:
        if job_id not in _open:
            c.execute("UPDATE jobs SET status='done', finished_at=? WHERE id=?", (int(time.time()), job_id))
        else:
            c.execute("UPDATE jobs SET status='waiting' WHERE id=? AND status='running'", (job_id,))
    _open.discard(job_id)
    return job_id
