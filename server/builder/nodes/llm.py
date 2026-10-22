"""Genova-style agent runner on OpenRouter (agent = JSON file: system_role, model, samples).

Same file contract as genova_web/chat.py (`system_role`, `sample_question`, `sample_answer`,
`model`) plus an optional `reasoning_effort`. Responses are cached on disk by request hash.
The API key is read from OPENROUTER_API_KEY and never written anywhere.
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://openrouter.ai/api/v1/chat/completions"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nodes"))
from library import record  # noqa: E402
from api_budget import active_budget, BudgetError
CACHE = Path(os.environ.get("GENOVA_VR_CACHE", ROOT / ".cache")) / "llm"


class LLMError(RuntimeError):
    pass


def extract_json(text: str):
    """Parse the first JSON object in a model reply (tolerates fences and prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    depth = 0
    in_str = esc = False
    for i in range(start, len(text)) if start >= 0 else []:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise LLMError("no JSON object in model reply")


class Agent:
    def __init__(self, path):
        self.path = Path(path)
        cfg = json.loads(self.path.read_text())
        self.system = cfg["system_role"]
        # GENOVA_LLM_MODEL (for example "openai/gpt-6.1-sol" or any OpenRouter model id)
        # overrides the model named in the agent file.
        self.model = os.environ.get("GENOVA_LLM_MODEL") or cfg.get("model", "openai/gpt-6.1-sol")
        self.effort = cfg.get("reasoning_effort", "medium")
        self.sample_q = cfg.get("sample_question") or ""
        self.sample_a = cfg.get("sample_answer") or ""
        self.max_tokens = cfg.get("max_tokens", 16000)

    def _messages(self, question, history, images=None):
        msgs = [{"role": "system", "content": self.system}]
        if self.sample_q and self.sample_a:
            msgs += [{"role": "user", "content": self.sample_q}, {"role": "assistant", "content": self.sample_a}]
        if images:
            import base64
            parts = [{"type": "text", "text": question}]
            for path in images:
                b64 = base64.b64encode(Path(path).read_bytes()).decode()
                parts.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}})
            msgs.append({"role": "user", "content": parts})
        else:
            msgs.append({"role": "user", "content": question})
        msgs += history or []
        return msgs

    def ask(self, question, history=None, use_cache=True, images=None) -> str:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise LLMError("OPENROUTER_API_KEY is not set")
        body = {"model": self.model, "messages": self._messages(question, history, images),
                "reasoning": {"effort": self.effort}, "max_tokens": self.max_tokens}
        budget = active_budget()
        if budget:
            budget.configure(body)
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        cached = CACHE / f"{digest}.txt"
        if use_cache and cached.exists():
            record("openrouter", self.model, cached=True, detail=self.path.stem)
            return cached.read_text()
        last = None
        for attempt in range(4):
            reservation = budget.reserve(body, self.path.stem) if budget else None
            settled = False
            req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
                "Authorization": "Bearer " + key, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    data = json.loads(r.read())
                if budget:
                    budget.settle(reservation, data)
                    settled = True
                    record("openrouter", self.model, cached=False, cost=data["usage"]["cost"], detail=self.path.stem)
                if "error" in data:
                    raise LLMError(str(data["error"])[:300])
                text = data["choices"][0]["message"]["content"] or ""
                if not text.strip():
                    raise LLMError("empty reply")
                CACHE.mkdir(parents=True, exist_ok=True)
                cached.write_text(text)
                if not budget:
                    record("openrouter", self.model, cached=False, cost=(data.get("usage") or {}).get("cost"), detail=self.path.stem)
                return text
            except BudgetError:
                raise
            except Exception as e:
                if budget and not settled:
                    budget.uncertain(reservation, e)
                    raise BudgetError("request failed with unverified cost; no automatic paid retry") from e
                if not isinstance(e, (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, LLMError)):
                    raise
                last = e
                time.sleep(2 ** attempt)
        raise LLMError(f"OpenRouter failed after retries: {last}")

    def ask_json(self, question, validate=None, retries=3):
        """Ask for JSON; if `validate(obj)` returns problems, feed them back and retry."""
        history, problems = [], None
        for attempt in range(retries + 1):
            text = self.ask(question, history, use_cache=(attempt == 0))
            try:
                obj = extract_json(text)
                problems = validate(obj) if validate else []
            except (json.JSONDecodeError, LLMError) as e:
                obj, problems = None, [f"reply was not valid JSON: {e}"]
            if not problems:
                return obj
            history = [{"role": "assistant", "content": text},
                       {"role": "user", "content": "The JSON has these problems; return the FULL corrected JSON only:\n- "
                        + "\n- ".join(problems[:15])}]
        raise LLMError("could not obtain valid JSON: " + "; ".join(problems[:5]))
