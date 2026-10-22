import copy
import fcntl
import json
import os
import struct
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


class BudgetError(RuntimeError):
    pass


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def amount(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise BudgetError("invalid budget amount")
    return result


class Budget:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def locked(self):
        with self.path.with_suffix(self.path.suffix + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def save(self, state):
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def initialize(self, limit, margin, model, pricing_snapshot):
        limit, margin = amount(limit), amount(margin)
        if model != "openai/gpt-6.1-sol":
            raise BudgetError("reservation rates have only been verified for openai/gpt-6.1-sol")
        if not limit > margin:
            raise BudgetError("budget must exceed the safety margin")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.locked():
            if self.path.exists():
                raise BudgetError("budget already exists; never reset an existing campaign")
            self.save({"created_at_utc": timestamp(), "limit_usd": str(limit),
                       "safety_margin_usd": str(margin), "model": model,
                       "prompt_reservation_usd_per_token": "0.000005",
                       "completion_reservation_usd_per_token": "0.000015",
                       "routing": {"only": ["openai"], "order": ["openai"],
                                   "allow_fallbacks": False, "require_parameters": True,
                                   "max_price": {"prompt": 4, "completion": 15, "request": 0, "image": 0}},
                       "pricing_snapshot": pricing_snapshot, "frozen": False, "calls": []})

    def read(self):
        with self.locked():
            return json.loads(self.path.read_text())

    def configure(self, body):
        state = self.read()
        if body["model"] != state["model"] or state["frozen"]:
            raise BudgetError("budget is frozen or model is not authorized")
        body["provider"] = state["routing"]

    def reserve(self, body, agent):
        messages = copy.deepcopy(body["messages"])
        image_tokens = 0
        for message in messages:
            if not isinstance(message.get("content"), list):
                continue
            for part in message["content"]:
                if part.get("type") == "image_url":
                    import base64
                    source = part["image_url"]["url"]
                    if not source.startswith("data:image/png;base64,"):
                        raise BudgetError("budget only supports bounded local PNG images")
                    raw = base64.b64decode(source.split(",", 1)[1], validate=True)
                    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
                        raise BudgetError("budget image is not a PNG")
                    width, height = struct.unpack(">II", raw[16:24])
                    if not 0 < width <= 4096 or not 0 < height <= 4096:
                        raise BudgetError("budget image dimensions exceed the verified pilot bounds")
                    part["image_url"]["url"] = "bounded-local-png"
                    image_tokens += 65536
        input_bound = len(json.dumps(messages, ensure_ascii=False).encode()) + 8192 + image_tokens
        if not isinstance(body["max_tokens"], int) or not 0 < body["max_tokens"] <= 128000:
            raise BudgetError("invalid completion limit")
        completion_bound = body["max_tokens"] + 1024
        with self.locked():
            state = json.loads(self.path.read_text())
            if state["frozen"] or body["model"] != state["model"]:
                raise BudgetError("budget is frozen or model is not authorized")
            if body.get("provider") != state["routing"]:
                raise BudgetError("budget routing must not be modified")
            ceiling = (amount(state["prompt_reservation_usd_per_token"]) * input_bound
                       + amount(state["completion_reservation_usd_per_token"]) * completion_bound)
            committed = sum((amount(call.get("actual_usd", call["reserved_usd"]))
                             for call in state["calls"]), Decimal("0"))
            available = amount(state["limit_usd"]) - amount(state["safety_margin_usd"]) - committed
            if ceiling > available:
                raise BudgetError(f"request blocked before spending: reservation ${ceiling} exceeds remaining ${available}")
            identifier = str(uuid.uuid4())
            state["calls"].append({"id": identifier, "agent": agent, "started_at_utc": timestamp(),
                                   "status": "reserved", "reserved_usd": str(ceiling),
                                   "input_token_bound": input_bound, "completion_token_bound": completion_bound})
            self.save(state)
            return identifier

    def settle(self, identifier, response):
        usage = response.get("usage") or {}
        cost = usage.get("cost")
        with self.locked():
            state = json.loads(self.path.read_text())
            call = next(entry for entry in state["calls"] if entry["id"] == identifier)
            if call["status"] != "reserved":
                raise BudgetError("call already settled")
            call.update({"response_id": response.get("id"), "provider": response.get("provider"),
                         "response_model": response.get("model"), "usage": usage,
                         "finished_at_utc": timestamp()})
            if cost is None:
                state["frozen"] = True
                call["status"] = "cost-unverified"
                self.save(state)
                raise BudgetError("provider did not return a cost; reservation retained, campaign stopped")
            try:
                actual = amount(cost)
            except BudgetError:
                state["frozen"] = True
                call["status"] = "cost-unverified"
                self.save(state)
                raise
            call.update({"actual_usd": str(actual), "status": "settled"})
            if actual > amount(call["reserved_usd"]):
                state["frozen"] = True
                self.save(state)
                raise BudgetError("provider charge exceeded reservation; campaign stopped")
            self.save(state)

    def uncertain(self, identifier, error):
        with self.locked():
            state = json.loads(self.path.read_text())
            call = next(entry for entry in state["calls"] if entry["id"] == identifier)
            call.update({"status": "cost-unverified", "error_type": type(error).__name__})
            state["frozen"] = True
            self.save(state)


def active_budget():
    path = os.environ.get("GENOVA_VR_API_BUDGET")
    return Budget(path) if path else None


def block_unmetered(service):
    if active_budget():
        raise BudgetError(f"{service} is disabled: the campaign only authorizes metered OpenRouter calls")
