
from orco import Runtime, LocalExecutor

import itertools
import random
import time
import threading
import os

if os.path.isfile("test.db"):
    os.unlink("test.db")

rt = Runtime("test.db")

executor = LocalExecutor(heartbeat_interval=1, n_processes=1)
rt.register_executor(executor)

executor2 = LocalExecutor(heartbeat_interval=1)
executor2._debug_do_not_start_heartbeat = True
rt.register_executor(executor2)

executor3 = LocalExecutor(heartbeat_interval=1)
rt.register_executor(executor3)
executor3.stop()

c_sleepers = rt.register_collection("sleepers", lambda c: time.sleep(c))
c_bedrooms = rt.register_collection("bedrooms", lambda c, d: None, lambda c: [c_sleepers.ref(x) for x in c["sleepers"]])

c_bedrooms.compute({"sleepers": [0.1]})
t = threading.Thread(target=(lambda: c_bedrooms.compute({"sleepers": list(range(10))})))
t.start()

time.sleep(0.5)  # To solve a problem with ProcessPool, fix waits for Python3.7

c = rt.register_collection("hello")
c.insert("e1", "ABC")
c.insert("e2", "A" * (7 * 1024 * 1024 + 200000))


c = rt.register_collection("estee")
graphs = ["crossv", "fastcrossv", "gridcat"]
models = ["simple", "maxmin"]
scheduler = ["blevel", "random", {"name": "camp", "iterations": 1000}, {"name": "camp", "iterations": 2000}]
for g, m, s in itertools.product(graphs, models, scheduler):
    c.insert({"graph": g, "model": m, "scheduler": s}, random.randint(1, 30000))

c = rt.register_collection("collection with space in name")

rt.serve()