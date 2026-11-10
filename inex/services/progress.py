import logging

progress = logging.getLogger("inex.progress")


def _report(task, done, total, finished):
    shown = "?" if total is None else total
    progress.debug(
        "%s: %d of %s",
        task,
        done,
        shown,
        extra={"task": task, "done": done, "total": total, "finished": finished},
    )


def advance(task, done, total=None):
    _report(task, done, total, False)


def finish(task, done, total=None):
    _report(task, done, total, True)
