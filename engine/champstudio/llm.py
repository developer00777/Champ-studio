"""LLM calls by role, with structured output, validation, fallback and cost ledger.

    plan = await llm.structured("planner", system, user, schema, name="scene_plan")

The schema is sent as OpenRouter `response_format: json_schema` (strict). The
reply is validated locally with jsonschema against the *original* schema; on a
validation failure the model gets one repair turn with the errors. If the
primary model errors (or doesn't support json_schema), the role's fallback is
tried, and as a last resort `json_object` mode with the schema in the prompt.
"""

from __future__ import annotations

import copy
import json
import logging
import re

import jsonschema

from . import costs, roles
from .providers.base import ProviderError
from .providers.openrouter import OpenRouterClient

log = logging.getLogger("champstudio.llm")

_client_factory = OpenRouterClient  # tests swap this for a mock-transport client


def set_client_factory(factory) -> None:
    global _client_factory
    _client_factory = factory


def _client() -> OpenRouterClient:
    return _client_factory()


# ── schema helpers ───────────────────────────────────────────────────

_DROP_KEYS = {"default", "examples", "$comment", "title"}


def strictify(schema: dict) -> dict:
    """Copy of `schema` acceptable to strict structured-output providers:
    every object closes (additionalProperties: false) and lists all properties
    as required (optional ones become nullable); custom x-* keys are dropped."""
    s = copy.deepcopy(schema)

    def walk(node, props_map: bool = False):
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        if props_map:  # keys here are property names, not schema keywords
            return {k: walk(v) for k, v in node.items()}
        out = {k: walk(v, props_map=(k in ("properties", "$defs", "definitions")))
               for k, v in node.items() if not k.startswith("x-") and k not in _DROP_KEYS}
        if out.get("type") == "object" and "properties" in out:
            required = set(out.get("required") or [])
            for name, prop in out["properties"].items():
                if name not in required and isinstance(prop, dict) and "type" in prop:
                    t = prop["type"]
                    if isinstance(t, str) and t != "null":
                        prop["type"] = [t, "null"]
                    elif isinstance(t, list) and "null" not in t:
                        prop["type"] = [*t, "null"]
                    if "enum" in prop and None not in prop["enum"]:
                        prop["enum"] = [*prop["enum"], None]
            out["required"] = list(out["properties"].keys())
            out["additionalProperties"] = False
        return out

    return walk(s)


def _drop_nulls(value, schema: dict):
    """Strict mode returns null for optional fields; remove them before validation."""
    if isinstance(value, dict):
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
        return {
            k: _drop_nulls(v, props.get(k, {}))
            for k, v in value.items()
            if not (v is None and k not in required)
        }
    if isinstance(value, list):
        item_schema = schema.get("items", {}) if isinstance(schema, dict) else {}
        return [_drop_nulls(v, item_schema) for v in value]
    return value


def parse_json(text: str):
    """Parse a model reply that should be JSON (tolerates code fences / prose around it)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise
        end = max(text.rfind("}"), text.rfind("]"))
        return json.loads(text[start : end + 1])


def validation_errors(value, schema: dict) -> list[str]:
    v = jsonschema.Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
        for e in sorted(v.iter_errors(value), key=lambda e: list(e.absolute_path))
    ][:12]


# ── calls ────────────────────────────────────────────────────────────


def _record(role: str, model: str, usage: dict, project_id: str | None) -> float:
    cost = usage.get("cost")
    if cost is None:
        cost = costs.estimate_llm(model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        estimated = True
    else:
        estimated = False
    try:
        costs.record(kind="llm", model=model, role=role, cost=float(cost), estimated=estimated,
                     project_id=project_id,
                     meta={"prompt_tokens": usage.get("prompt_tokens"),
                           "completion_tokens": usage.get("completion_tokens")})
    except Exception:  # noqa: BLE001
        log.exception("ledger write failed")
    return float(cost)


async def complete(role: str, system: str, user: str, *, temperature: float = 0.4,
                   max_tokens: int = 2000, project_id: str | None = None) -> str:
    """Plain-text completion with fallback."""
    client = _client()
    last: Exception | None = None
    for model in roles.candidates(role):
        try:
            res = await client.chat(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            _record(role, model, res["usage"], project_id)
            return res["content"]
        except ProviderError as e:
            log.warning("llm %s/%s failed: %s", role, model, e)
            last = e
    raise ProviderError(f"All models for '{role}' failed: {last}")


async def structured(
    role: str,
    system: str,
    user: str | list,
    schema: dict,
    *,
    name: str = "result",
    temperature: float = 0.4,
    max_tokens: int = 16000,
    project_id: str | None = None,
):
    """JSON output validated against `schema`. `user` may be a string or content parts."""
    client = _client()
    strict = strictify(schema)
    last: Exception | None = None

    for model in roles.candidates(role):
        for mode in ("json_schema", "json_object"):
            messages = [
                {"role": "system", "content": system if mode == "json_schema" else
                 f"{system}\n\nReply with ONLY a JSON value matching this JSON Schema:\n{json.dumps(strict)}"},
                {"role": "user", "content": user},
            ]
            rf = ({"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": strict}}
                  if mode == "json_schema" else {"type": "json_object"})
            try:
                budget = max_tokens
                for attempt in range(2):  # one repair turn on invalid output
                    res = await client.chat(model=model, messages=messages, response_format=rf,
                                            temperature=temperature, max_tokens=budget,
                                            reasoning={"effort": "low"})
                    _record(role, model, res["usage"], project_id)
                    if res.get("finish_reason") == "length" and not res["content"].strip():
                        # a reasoning model spent the whole budget thinking: give it room, don't "repair"
                        log.info("llm %s/%s ran out of tokens while reasoning; retrying with more", role, model)
                        budget *= 2
                        res = await client.chat(model=model, messages=messages, response_format=rf,
                                                temperature=temperature, max_tokens=budget,
                                                reasoning={"effort": "low"})
                        _record(role, model, res["usage"], project_id)
                    try:
                        value = _drop_nulls(parse_json(res["content"]), schema)
                    except (json.JSONDecodeError, ValueError) as e:
                        errors = [f"not valid JSON: {e}"]
                        value = None
                    else:
                        errors = validation_errors(value, schema)
                    if not errors:
                        return value
                    log.info("llm %s/%s invalid output (attempt %d): %s", role, model, attempt + 1, errors)
                    messages = [
                        *messages,
                        {"role": "assistant", "content": res["content"]},
                        {"role": "user", "content": "That JSON does not match the schema. Fix these and "
                         "reply with the full corrected JSON only:\n- " + "\n- ".join(errors)},
                    ]
                last = ProviderError(f"{model} returned invalid JSON: {errors}")
            except ProviderError as e:
                last = e
                msg = str(e).lower()
                log.warning("llm %s/%s (%s) failed: %s", role, model, mode, e)
                if mode == "json_schema" and ("schema" in msg or "response_format" in msg or "400" in msg):
                    continue  # retry same model in json_object mode
                break  # other errors: next model
    raise ProviderError(f"All models for '{role}' failed to produce valid JSON: {last}")
