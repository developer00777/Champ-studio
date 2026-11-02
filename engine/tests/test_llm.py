"""LLM structured output, fallback, ledger, budgets and persistent jobs — all
through httpx.MockTransport, so no key is ever spent."""

import asyncio
import json

import httpx
import pytest

from champstudio import config, costs, jobs, llm
from champstudio.providers.base import ProviderError
from champstudio.providers.openrouter import OpenRouterClient

SCHEMA = {
    "type": "object",
    "required": ["title", "scenes"],
    "properties": {
        "title": {"type": "string", "maxLength": 40},
        "scenes": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["text"],
            "properties": {"text": {"type": "string"}, "seconds": {"type": "number", "x-kind": "number"}}}},
        "note": {"type": "string", "default": "x"},
    },
}


def mock_client(handler):
    transport = httpx.MockTransport(handler)
    llm.set_client_factory(lambda: OpenRouterClient(api_key="sk-test", transport=transport))


@pytest.fixture(autouse=True)
def restore_factory():
    yield
    llm.set_client_factory(OpenRouterClient)


def reply(content, cost=0.0004, model="m"):
    return httpx.Response(200, json={
        "model": model, "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": cost}})


def test_strictify_closes_objects_and_nulls_optionals():
    s = llm.strictify(SCHEMA)
    assert s["additionalProperties"] is False
    assert set(s["required"]) == {"title", "scenes", "note"}
    assert s["properties"]["note"]["type"] == ["string", "null"]
    item = s["properties"]["scenes"]["items"]
    assert item["required"] == ["text", "seconds"] and "x-kind" not in item["properties"]["seconds"]
    assert "default" not in s["properties"]["note"]


async def test_structured_sends_json_schema_and_validates():
    seen = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        seen.append(body)
        return reply(json.dumps({"title": "Q3 data ad", "scenes": [{"text": "Hook", "seconds": None}], "note": None}))

    mock_client(handler)
    out = await llm.structured("planner", "sys", "brief", SCHEMA, name="plan")
    assert out == {"title": "Q3 data ad", "scenes": [{"text": "Hook"}]}
    rf = seen[0]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert seen[0]["model"] == "anthropic/claude-haiku-5.5"


async def test_structured_repairs_invalid_output_once():
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        if len(calls) == 1:
            return reply(json.dumps({"title": "x" * 80, "scenes": []}))
        return reply("```json\n" + json.dumps({"title": "ok", "scenes": [{"text": "a"}]}) + "\n```")

    mock_client(handler)
    out = await llm.structured("planner", "sys", "brief", SCHEMA)
    assert out["title"] == "ok"
    assert "does not match the schema" in calls[1]["messages"][-1]["content"]


async def test_structured_falls_back_to_second_model_and_json_object():
    models = []

    def handler(req):
        body = json.loads(req.content)
        models.append((body["model"], body["response_format"]["type"]))
        if body["model"] == "anthropic/claude-haiku-5.5":
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"message": "response_format json_schema unsupported"}})
        return reply(json.dumps({"title": "t", "scenes": [{"text": "s"}]}))

    mock_client(handler)
    out = await llm.structured("planner", "sys", "u", SCHEMA)
    assert out["title"] == "t"
    assert models == [("anthropic/claude-haiku-5.5", "json_schema"),
                      ("openai/gpt-6-luna", "json_schema"), ("openai/gpt-6-luna", "json_object")]


async def test_all_models_failing_raises():
    mock_client(lambda req: httpx.Response(500, json={"error": {"message": "boom"}}))
    with pytest.raises(ProviderError):
        await llm.structured("planner", "s", "u", SCHEMA)


async def test_llm_calls_land_in_the_ledger():
    before = costs.spent()
    mock_client(lambda req: reply("hello", cost=0.25))
    assert await llm.complete("bulk", "s", "u") == "hello"
    assert costs.spent() == pytest.approx(before + 0.25)


def test_role_overrides_from_settings():
    config.update_settings(defaults={"roles": {"planner": {"model": "openai/gpt-6-luna"}}})
    try:
        from champstudio import roles
        assert roles.model_for("planner") == "openai/gpt-6-luna"
        assert roles.role("video_draft")["params"]["resolution"] == "720p"
    finally:
        config.update_settings(defaults={"roles": {}})


def test_estimates_and_budget_caps():
    assert costs.estimate_video("google/veo-3.1-lite", 10, "720p") == pytest.approx(0.30)
    assert costs.estimate_video("google/veo-3.1-fast", 8, "1080p", audio=True) == pytest.approx(0.96)
    costs.update_video_prices_from_catalog([{"id": "x/new", "pricing_skus": {"duration_seconds_480p": "0.04"}}])
    assert costs.estimate_video("x/new", 5, "480p") == pytest.approx(0.2)
    config.update_settings(defaults={"budget": {"monthly_usd": costs.spent() + 1.0}})
    try:
        costs.check_budget(0.5)
        with pytest.raises(costs.BudgetExceeded):
            costs.check_budget(5.0)
    finally:
        config.update_settings(defaults={"budget": {}})


async def test_spawned_jobs_persist_progress_and_results():
    async def body(h):
        h.stage("working", 0.5)
        await asyncio.sleep(0.01)
        return {"answer": 42}

    job = jobs.spawn("test", body, payload={"x": 1, "frame": "data:image/png;base64,AAAA"})
    done = await jobs.wait_for(job["id"], timeout_s=5)
    assert done["status"] == "completed" and done["result"] == {"answer": 42}
    assert done["x"] == 1 and done["frame"] == "<inline data>"
    assert any(j["id"] == job["id"] for j in jobs.list_jobs(kind="test"))


async def test_failed_and_canceled_jobs_resolve():
    async def boom(h):
        raise ValueError("nope")

    j = jobs.spawn("test", boom)
    assert (await jobs.wait_for(j["id"], timeout_s=5))["error"] == "ValueError: nope"

    async def slow(h):
        await asyncio.sleep(10)

    j2 = jobs.spawn("test", slow)
    await asyncio.sleep(0.05)
    jobs.cancel_job(j2["id"])
    assert (await jobs.wait_for(j2["id"], timeout_s=5))["status"] == "canceled"


async def test_restart_resumes_provider_jobs_and_fails_local_work():
    local = jobs.create_job("render", {"preset": "h264"}, status="running")
    remote = jobs.create_job("video", {"provider": "openrouter", "provider_job_id": "gen-1",
                                       "model": "m", "prompt": "p"}, status="in_progress")

    class Fake:
        provider = "openrouter"

        async def poll_video(self, _id):
            return {"status": "failed", "error": "upstream said no"}

    old = jobs.POLL_INTERVAL_S
    jobs.POLL_INTERVAL_S = 0
    try:
        n = await jobs.resume_after_restart(lambda p: Fake())
        assert n >= 1
        assert jobs.get_job(local["id"])["error"] == "interrupted by engine restart"
        done = await jobs.wait_for(remote["id"], timeout_s=5)
        assert done["error"] == "upstream said no"
    finally:
        jobs.POLL_INTERVAL_S = old


async def test_transcribe_and_music_payloads():
    seen = {}

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        seen[req.url.path] = body
        if req.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "hi there", "duration": 1.2, "words": [
                {"word": "hi", "start": 0.0, "end": 0.4}, {"word": "(laughter)", "start": 0.4, "end": 0.6,
                                                            "type": "audio_event"},
                {"word": "there", "start": 0.6, "end": 1.1}], "usage": {"cost": 0.0002}})
        sse = ("data: " + json.dumps({"choices": [{"delta": {"audio": {"data": "SUQz"}}}]}) + "\n\n"
               "data: " + json.dumps({"choices": [{"delta": {"audio": {"data": "AAAA"}}}],
                                      "usage": {"cost": 0.04}}) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    c = OpenRouterClient(api_key="k", transport=httpx.MockTransport(handler))
    t = await c.transcribe(audio=b"RIFF", audio_format="wav", model="openai/whisper-large-v3")
    assert [w["word"] for w in t["words"]] == ["hi", "there"]
    body = seen["/api/v1/audio/transcriptions"]
    assert body["response_format"] == "verbose_json" and "word" in body["timestamp_granularities"]
    m = await c.generate_music(prompt="calm corporate", model="google/lyria-3-clip-preview")
    assert m.data == b"ID3\x00\x00\x00" and m.cost == 0.04
    assert seen["/api/v1/chat/completions"]["stream"] is True
