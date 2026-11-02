"""A fake OpenRouter (httpx.MockTransport) that returns real media bytes, so
Director/template pipelines run end to end in tests without a key."""

from __future__ import annotations

import base64
import io
import json
import re
import subprocess

import httpx
from PIL import Image

from champstudio import jobs, llm, producers, transcribe
from champstudio.providers.openrouter import OpenRouterClient


def _ff(args: list[str]) -> bytes:
    return subprocess.run(["ffmpeg", "-v", "error", *args, "-"], capture_output=True, check=True).stdout


TONE_MP3 = None
CLIP_MP4 = None


def tone_mp3() -> bytes:
    global TONE_MP3
    if TONE_MP3 is None:
        TONE_MP3 = _ff(["-f", "lavfi", "-i", "sine=frequency=220:duration=1.6", "-f", "mp3"])
    return TONE_MP3


def clip_mp4() -> bytes:
    global CLIP_MP4
    if CLIP_MP4 is None:
        CLIP_MP4 = _ff(["-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=4", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-movflags", "frag_keyframe+empty_moov", "-f", "mp4"])
    return CLIP_MP4


def png(color=(242, 103, 34)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), color).save(buf, "PNG")
    return buf.getvalue()


PLAN = {
    "title": "Verified data, faster pipeline",
    "logline": "Show RevOps leaders how verified contacts shorten sales cycles.",
    "music_mood": "corporate-upbeat",
    "cta": "Book a demo",
    "scenes": [
        {"purpose": "hook", "voiceover": "Still chasing bounced emails?", "on_screen_text": "Bounced again?",
         "visual": "Frustrated sales rep at a laptop, moody office", "camera": "slow push-in",
         "visual_type": "ai_video", "duration_s": 3, "transition": "cut"},
        {"purpose": "stat", "voiceover": "Ninety nine percent accuracy across 120 million contacts.",
         "on_screen_text": "99% accurate", "visual": "abstract data network", "visual_type": "text_card",
         "duration_s": 3.5, "transition": "crossfade"},
        {"purpose": "cta", "voiceover": "Book a demo today.", "on_screen_text": "Grow faster",
         "visual": "confident team in a bright modern office", "visual_type": "ai_image", "duration_s": 3,
         "transition": "dip-black"},
    ],
}


def fake_from_schema(schema: dict, name: str = "value"):
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    if "enum" in schema:
        return next(v for v in schema["enum"] if v is not None)
    if t == "object":
        return {k: fake_from_schema(v, k) for k, v in (schema.get("properties") or {}).items()
                if k in (schema.get("required") or [])}
    if t == "array":
        n = max(1, schema.get("minItems", 1))
        return [fake_from_schema(schema.get("items", {}), name) for _ in range(n)]
    if t in ("number", "integer"):
        return schema.get("minimum", 3)
    pattern = schema.get("pattern", "")
    if "#" in pattern or "[0-9A-Fa-f]{6}" in pattern:
        return "#F26722"
    if pattern and "[0-9" in pattern:
        return "42%"
    text = f"Sample {name.replace('_', ' ')}"
    return text[: schema.get("maxLength", 200)]


class FakeOpenRouter:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.patch_ops: list[dict] | None = None
        self.fail_video = False
        self.canned: dict[str, dict] = {}      # json_schema name -> reply content
        self.words: list[dict] | None = None   # transcription words override

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        body = json.loads(req.content) if req.content and req.method == "POST" else {}
        self.calls.append((path, body))
        if path.endswith("/chat/completions"):
            if body.get("stream"):
                b64 = base64.b64encode(tone_mp3()).decode()
                sse = f"data: {json.dumps({'choices': [{'delta': {'audio': {'data': b64}}}], 'usage': {'cost': 0.04}})}\n\ndata: [DONE]\n\n"
                return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
            rf = body.get("response_format") or {}
            name = (rf.get("json_schema") or {}).get("name", "")
            if name in self.canned:
                content = self.canned[name]
            elif name == "video_plan":
                content = PLAN
            elif name == "plan_patch":
                content = {"summary": "Changed the hook line.", "ops": self.patch_ops or [
                    {"op": "replace", "path": "/scenes/0/voiceover", "value": "Tired of bounced emails?"}]}
            elif rf.get("json_schema"):
                content = fake_from_schema(rf["json_schema"]["schema"])
            else:
                content = {"ok": True}
            return httpx.Response(200, json={"model": body["model"], "choices": [
                {"message": {"content": json.dumps(content)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 400, "cost": 0.0003}})
        if path.endswith("/images"):
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode(),
                                                       "media_type": "image/png"}], "usage": {"cost": 0.01}})
        if path.endswith("/audio/speech"):
            return httpx.Response(200, content=tone_mp3(), headers={"content-type": "audio/mpeg"})
        if path.endswith("/audio/transcriptions"):
            words = self.words or [{"word": "words", "start": 0.1, "end": 0.6}]
            return httpx.Response(200, json={"text": " ".join(w["word"] for w in words), "duration": 1.6,
                                             "words": words, "usage": {"cost": 0.0001}})
        if path.endswith("/videos") and req.method == "POST":
            return httpx.Response(200, json={"id": f"vid{len(self.calls)}"})
        if re.search(r"/videos/[^/]+$", path) and req.method == "GET":
            if self.fail_video:
                return httpx.Response(200, json={"status": "failed", "error": {"message": "content policy"}})
            return httpx.Response(200, json={"status": "completed", "unsigned_urls": ["https://cdn.fake/out.mp4"],
                                             "usage": {"cost": 0.12}})
        if req.url.host == "cdn.fake":
            return httpx.Response(200, content=clip_mp4())
        return httpx.Response(404, json={"error": {"message": f"unmocked {path}"}})

    def install(self):
        transport = httpx.MockTransport(self.handler)
        factory = lambda: OpenRouterClient(api_key="sk-fake", transport=transport)  # noqa: E731
        llm.set_client_factory(factory)
        producers.set_client_factory(factory)
        transcribe.set_client_factory(factory)
        jobs.POLL_INTERVAL_S = 0
        return self

    @staticmethod
    def uninstall():
        llm.set_client_factory(OpenRouterClient)
        producers.set_client_factory(OpenRouterClient)
        transcribe.set_client_factory(OpenRouterClient)
        jobs.POLL_INTERVAL_S = 5

    def count(self, suffix: str) -> int:
        return sum(1 for p, _ in self.calls if p.endswith(suffix))
