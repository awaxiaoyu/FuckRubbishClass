"""Local JSONL peer for process, cancellation and stream-contract tests."""

import json
import os
from queue import Queue
import sys
from threading import Event, Thread
import time

requests = Queue()
cancel = Event()
confirmations = Queue()


def write(value):
    print(json.dumps(value), flush=True)


def read():
    for line in sys.stdin:
        request = json.loads(line)
        if request["operation"] == "cancel":
            cancel.set()
        elif request["operation"] == "confirm_ai_only_submit_response":
            confirmations.put(request["confirmed"])
        else:
            requests.put(request)
    requests.put(None)


Thread(target=read, daemon=True).start()
while (request := requests.get()) is not None:
    operation = request["operation"]
    if operation == "progress":
        write({"stream": "progress", "event": {"type": "progress", "message": "actual progress", "current": 1, "total": 2}})
    elif operation == "confirm":
        write({"stream": "confirmation", "event": {"confirmationId": "test", "aiOnlyCount": 1}})
        write({"ok": True, "confirmed": confirmations.get(timeout=3)})
        continue
    elif operation == "cancel_test":
        cancel.clear()
        write({"stream": "progress", "event": {"message": "waiting for cancel"}})
        cancel.wait(10)
        write({"ok": False, "error": "cancelled"})
        continue
    elif operation == "hang":
        write({"stream": "progress", "event": {"message": "cannot cancel cooperatively"}})
        time.sleep(30)
    elif operation == "error":
        write({"ok": False, "error": "expected error"})
        continue
    elif operation == "invalid":
        print("invalid JSON", flush=True)
        continue
    write({"ok": True, "operation": operation, "pid": os.getpid(), "data": os.environ["FRC_BACKEND_DATA_DIR"]})
