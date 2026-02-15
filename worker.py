# worker.py
#
# Windows note:
# The default RQ Worker relies on Unix process APIs (fork/wait4) and will crash on Windows.
# On Windows, use rq-win's WindowsWorker (dev/testing oriented). [web:204]
# On non-Windows, use RQ's standard Worker (or SpawnWorker if you prefer). [web:207]

import os
os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"

from app import create_app
from app.jobs.queue import get_redis_connection, get_queue_names


def _select_worker_class():
    # Windows
    if os.name == "nt":
        try:
            # rq-win exposes WindowsWorker at rq_win.WindowsWorker. [web:204][web:201]
            from rq_win import WindowsWorker  # type: ignore
            return WindowsWorker
        except Exception as e:
            raise RuntimeError(
                "Running on Windows but rq-win is not installed.\n"
                "Fix: pip install rq-win\n"
                "Then rerun: python worker.py"
            ) from e



    # Non-Windows (Linux/macOS)
    from rq import Worker  # type: ignore
    return Worker


def main():
    app = create_app()
    redis_conn = get_redis_connection(app.config["REDIS_URL"])
    WorkerClass = _select_worker_class()

    with app.app_context():
        worker = WorkerClass(get_queue_names(), connection=redis_conn)
        worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
