import argparse
import json
import sys

from .executor import LocalExecutor
from .collection import CollectionRef


def _command_serve(runtime, args):
    runtime.serve()


def _command_compute(runtime, args):
    runtime.register_executor(LocalExecutor(n_processes=1))
    ref = CollectionRef(args.collection).ref(json.loads(args.config))
    print(runtime.compute(ref).value)


def _command_remove(runtime, args):
    collection = runtime.collections.get(args.collection)
    if collection is None:
        raise Exception("Unknown collection '%s'", args.collection)
    collection.remove(json.loads(args.config))


def _parse_args(runtime):
    parser = argparse.ArgumentParser("orco", description="Organized Computing")
    sp = parser.add_subparsers(title="command")
    parser.set_defaults(command=None)

    # SERVE
    p = sp.add_parser("serve")
    p.set_defaults(command=_command_serve)

    # COMPUTE
    p = sp.add_parser("compute")
    p.add_argument("collection")
    p.add_argument("config")
    p.set_defaults(command=_command_compute)

    # REMOVE
    p = sp.add_parser("remove")
    p.add_argument("collection")
    p.add_argument("config")
    p.set_defaults(command=_command_remove)

    return parser.parse_args()


def run_cli(runtime):
    try:
        args = _parse_args(runtime)
        if args.command is None:
            print("No command provided", file=sys.stderr)
        else:
            args.command(runtime, args)
    finally:
        runtime.stop()