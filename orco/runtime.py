
import cloudpickle
import threading
import logging
import tqdm
import collections

from .db import DB
from .collection import Collection, Ref
from .executor import Executor, LocalExecutor, Task
from .utils import format_time

logger = logging.getLogger(__name__)


class Runtime:

    def __init__(self, db_path, executor: Executor=None):
        self.db = DB(db_path)
        self.db.init()

        self._executor = executor
        self._collections = {}
        self._lock = threading.Lock()

        self.executors = []

        logging.debug("Starting runtime %s (db=%s)", self, db_path)

        if executor:
            self.register_executor(executor)

    def stop(self):
        logger.debug("Stopping runtime %s", self)
        for executor in self.executors:
            logger.debug("Stopping executor %s", executor)
            executor.stop()

    def register_executor(self, executor):
        logger.debug("Registering executor %s", executor)
        executor.runtime = self
        self.db.register_executor(executor)
        executor.start()
        self.executors.append(executor)

    def unregister_executor(self, executor):
        logger.debug("Unregistering executor %s", executor)
        self.executors.remove(executor)
        self.db.stop_executor(executor.id)

    def register_collection(self, name, build_fn=None, dep_fn=None):
        with self._lock:
            if name in self._collections:
                raise Exception("Collection already registered")
            self.db.ensure_collection(name)
            collection = Collection(self, name, build_fn=build_fn, dep_fn=dep_fn)
            self._collections[name] = collection
            return collection

    @property
    def collections(self):
        with self._lock:
            return self._collections.copy()

    def collection_summaries(self):
        return self.db.collection_summaries()

    def entry_summaries(self, collection_name):
        return self.db.entry_summaries(collection_name)

    def executor_summaries(self):
        return self.db.executor_summaries()

    def update_heartbeat(self, id):
        self.db.update_heartbeat(id)

    def serve(self, port=8550, debug=False, testing=False):
        from .rest import init_service
        app = init_service(self)
        if testing:
            app.testing = True
            return app
        else:
            app.run(port=port, debug=debug, use_reloader=False)

    def get_entry(self, ref):
        collection = ref.collection
        entry = self.db.get_entry_no_config(collection.name, collection.make_key(ref.config))
        entry.config = ref.config
        return entry

    def compute_refs(self, refs):
        tasks = {}
        global_deps = set()
        exists = set()

        def make_task(ref):
            ref_key = ref.ref_key()
            if ref_key in exists:
                return ref
            task = tasks.get(ref_key)
            if task is not None:
                return task
            collection = ref.collection
            state = self.db.get_entry_state(collection.name, ref_key[1])
            if state == "finished":
                exists.add(ref_key)
                return ref
            if state == "announced":
                raise Exception("Computation needs announced but not finished entries, it is not supported now: {}".format(ref))
            if state is None and collection.dep_fn:
                deps = collection.dep_fn(ref.config)
                for r in deps:
                    assert isinstance(r, Ref)
                    global_deps.add((r, ref))
                inputs = [make_task(r) for r in deps]
            else:
                inputs = None
            if state is None and collection.build_fn is None:
                raise Exception("Computation depends on missing configuration '{}' in a fixed collection".format(ref))
            task = Task(ref, inputs)
            tasks[ref_key] = task
            return task

        if len(self.executors) == 0:
            raise Exception("No executors registered")
        executor = self.executors[0]

        requested_tasks = [make_task(ref) for ref in  refs]
        need_to_compute_refs = [task.ref for task in tasks.values()]
        logger.debug("Announcing refs %s at worker %s", need_to_compute_refs, executor.id)
        if not self.db.announce_entries(executor.id, need_to_compute_refs, global_deps):
            raise Exception("Was not able to announce task into DB")
        del global_deps  # we do not this anymore, and .run may be long

        try:
            tasks_per_collection = collections.Counter([t.ref.collection.name for t in tasks.values()])
            print("Scheduled tasks  |     # | Expected comp. time (per entry)\n"
                  "-----------------+-------+--------------------------------")
            for col, count in sorted(tasks_per_collection.items()):
                stats = self.db.get_run_stats(col)
                if stats["avg"] is None:
                    print("{:<17}| {:>5} | N/A".format(col, count))
                else:
                    print("{:<17}| {:>5} | {:>8} +- {}".format(
                        col, count, format_time(stats["avg"]), format_time(stats["stdev"])))
            print("-----------------+-------+--------------------------------")

            return executor.run(tasks, requested_tasks)
        except:
            self.db.unannounce_entries(executor.id, need_to_compute_refs)
            raise