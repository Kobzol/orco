import logging
import multiprocessing
import threading
import time

from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from collections import namedtuple

import cloudpickle
import tqdm
import traceback

from .internals.taskoptions import TaskOptions
from .internals.reftools import resolve_ref_keys, ref_to_refkey, collect_ref_keys
from .internals.db import DB
from .internals.task import Task
from .internals.executor import Executor

from .ref import RefKey
from .report import Report

logger = logging.getLogger(__name__)


class TaskFailException(Exception):
    """Exception thrown when task in an executor failed"""
    pass


class _TaskFailure:

    def __init__(self, ref_key):
        self.ref_key = ref_key

    def message(self):
        raise NotImplementedError()

    def report_type(self):
        raise NotImplementedError()


class _TaskError(_TaskFailure):

    def __init__(self, ref_key, exception_str, traceback):
        super().__init__(ref_key)
        self.exception_str = exception_str
        self.traceback = traceback

    def message(self):
        return "Task failed: {}\n{}".format(self.exception_str, self.traceback)

    def report_type(self):
        return "error"


class _TaskTimeout(_TaskFailure):

    def __init__(self, ref_key, timeout):
        super().__init__(ref_key)
        self.timeout = timeout

    def message(self):
        return "Task timeouted after {} seconds".format(self.timeout)

    def report_type(self):
        return "timeout"


def _heartbeat(runtime, id, event, heartbeat_interval):
    while not event.is_set():
        runtime.db.update_heartbeat(id)
        time.sleep(heartbeat_interval)


class LocalExecutor(Executor):
    """
    Executor that performs computations locally.

    Executor spawns build functions in parallel. By default it spawns at most N
    build functions where N is number of cpus of the local machine. This can be
    configured via argument `n_processes` in the constructor.

    All methods of executor are considered internal. Users should use it only
    via Runtime by method `register_executor`.

    User may decreate heatbeat_interval (in seconds) if faster detection of
    hard-crash of executor is desired. But it should not be lesser than 1s
    otherwise performance issues may raises because of hammering DB. Note that
    it serves only for detecting crashes when whole Python interpreter is lost,
    it is not needed for normal exceptions.
    """

    _debug_do_not_start_heartbeat = False

    def __init__(self, heartbeat_interval=7, n_processes=None):
        super().__init__("local", "0.0", "{} cpus".format(multiprocessing.cpu_count()),
                         heartbeat_interval)
        self.heartbeat_thread = None
        self.heartbeat_stop_event = None

        self.pool = None
        self.n_processes = n_processes

    def get_stats(self):
        return self.stats

    def stop(self):
        if self.heartbeat_stop_event:
            self.heartbeat_stop_event.set()
        self.runtime.unregister_executor(self)
        self.runtime = None

    def start(self):
        assert self.runtime
        assert self.id is not None

        if not self._debug_do_not_start_heartbeat:
            self.heartbeat_stop_event = threading.Event()
            self.heartbeat_thread = threading.Thread(
                target=_heartbeat,
                args=(self.runtime, self.id, self.heartbeat_stop_event, self.heartbeat_interval))
            self.heartbeat_thread.daemon = True
            self.heartbeat_thread.start()

        self.pool = ProcessPoolExecutor(max_workers=self.n_processes)

    def _init(self, tasks):
        consumers = {}
        waiting_deps = {}
        ready = []

        for task in tasks:
            count = 0
            for inp in task.inputs:
                if isinstance(inp, Task):
                    count += 1
                    c = consumers.get(inp)
                    if c is None:
                        c = []
                        consumers[inp] = c
                    c.append(task)
            if count == 0:
                ready.append(task)
            waiting_deps[task] = count
        return consumers, waiting_deps, ready

    def run(self, all_tasks, continue_on_error):

        def process_unprocessed():
            logging.debug("Writing into db: %s", unprocessed)
            db.set_entry_values(self.id, unprocessed, self.stats, pending_reports)
            if pending_reports:
                del pending_reports[:]
            for raw_entry in unprocessed:
                ref_key = RefKey(raw_entry.collection_name, raw_entry.key)
                task = all_tasks[ref_key]
                #col_progressbars[ref_key[0]].update()
                for c in consumers.get(task, ()):
                    waiting_deps[c] -= 1
                    w = waiting_deps[c]
                    if w <= 0:
                        assert w == 0
                        waiting.add(submit(c))

        def submit(task):
            ref = task.ref
            pickled_fns = pickle_cache.get(ref.collection_name)
            if pickled_fns is None:
                collection = self.runtime._get_collection(task.ref)
                pickled_fns = cloudpickle.dumps((collection.build_fn, collection.make_raw_entry))
                pickle_cache[ref.collection_name] = pickled_fns
            return pool.submit(_run_task, db.path, pickled_fns, task.ref.ref_key(), task.ref.config,
                               ref_to_refkey(task.dep_value))

        self.stats = {"n_tasks": len(all_tasks), "n_completed": 0}

        pending_reports = []
        all_tasks = {ref.ref_key(): task for (ref, task) in all_tasks.items()}
        pickle_cache = {}
        pool = self.pool
        db = self.runtime.db
        db.update_stats(self.id, self.stats)
        consumers, waiting_deps, ready = self._init(all_tasks.values())
        waiting = [submit(task) for task in ready]
        del ready

        #col_progressbars = {}
        #for i, (col, count) in enumerate(tasks_per_collection.items()):
        #    col_progressbars[col] = tqdm.tqdm(desc=col, total=count, position=i)

        progressbar = tqdm.tqdm(total=len(all_tasks))  #  , position=i+1)
        unprocessed = []
        last_write = time.time()
        errors = []
        try:
            while waiting:
                wait_result = wait(
                    waiting, return_when=FIRST_COMPLETED, timeout=1 if unprocessed else None)
                waiting = wait_result.not_done
                for f in wait_result.done:
                    self.stats["n_completed"] += 1
                    progressbar.update()
                    result = f.result()
                    if isinstance(result, _TaskFailure):
                        ref_key = result.ref_key
                        config = db.get_config(ref_key[0], ref_key[1])
                        message = result.message()
                        report = Report(
                            result.report_type(),
                            self.id,
                            message,
                            collection_name=ref_key[0],
                            config=config)
                        if continue_on_error:
                            errors.append(ref_key)
                            pending_reports.append(report)
                        else:
                            db.insert_report(report)

                            raise TaskFailException("{} ({}/{})".format(
                                message, result.ref_key[0], repr(result.ref_key[1])))
                        continue
                    logger.debug("Task finished: %s/%s", result.collection_name, result.key)
                    unprocessed.append(result)
                if unprocessed and (not waiting or time.time() - last_write > 1):
                    process_unprocessed()
                    unprocessed = []
                    last_write = time.time()
            #    db.update_stats(self.id, self.stats)
            #for p in col_progressbars.values():
            #    p.close()
            db.set_entry_values(self.id, unprocessed, self.stats, pending_reports)
            return errors
        finally:
            progressbar.close()
            for f in waiting:
                f.cancel()


_per_process_db = None


def _run_task(db_path, fns, ref_key, config, dep_value):
    global _per_process_db
    try:
        if _per_process_db is None:
            _per_process_db = DB(db_path, threading=False)
        build_fn, finalize_fn = cloudpickle.loads(fns)

        result = []

        def run():
            start_time = time.time()
            deps = collect_ref_keys(dep_value)
            ref_map = {ref: _per_process_db.get_entry(ref.collection_name, ref.key) for ref in deps}
            value = build_fn(config, resolve_ref_keys(dep_value, ref_map))
            end_time = time.time()
            result.append(
                finalize_fn(ref_key.collection_name, ref_key.key, None, value,
                            end_time - start_time))

        options = TaskOptions.parse_from_config(config)
        if options.timeout:
            thread = threading.Thread(target=run)
            thread.daemon = True
            thread.start()
            thread.join(options.timeout)
            if thread.is_alive():
                return _TaskTimeout(ref_key, options.timeout)
        else:
            run()
        return result[0]
    except Exception as exception:
        return _TaskError(ref_key, str(exception), traceback.format_exc())