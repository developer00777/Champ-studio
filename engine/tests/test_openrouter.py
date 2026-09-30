"""OpenRouter provider tests — request bodies and response handling via httpx.MockTransport."""

import json

import httpx
import pytest

from champstudio.providers.base import ProviderError
from champstudio.providers.openrouter import OpenRouterClient, audio_part, image_part


def make_client(handler):
    return OpenRouterClient(api_key="sk-or-test", transport=httpx.MockTransport(handler))


def test_content_part_helpers_match_api_reference():
    assert image_part("data:image/png;base64,AAA") == {
        "type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"},
    }
    assert audio_part("https://x/y.mp3") == {"type": "audio_url", "audio_url": {"url": "https://x/y.mp3"}}


async def test_submit_video_sends_documented_frame_and_reference_shapes():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, json={"id": "gen-vid-1"})

    job = await make_client(handler).submit_video(
        prompt="walks forward",
        model="kwaivgi/kling-v3.0-std",
        duration=5,
        first_frame="data:image/png;base64,AAA",
        last_frame="data:image/png;base64,BBB",
        input_references=["data:image/png;base64,CCC"],
        audio_references=["data:audio/mpeg;base64,DDD"],
        provider_options={"heygen": {"voice_id": "v1"}},
    )
    assert job == "gen-vid-1"
    assert seen["url"] == "https://openrouter.ai/api/v1/videos"
    assert seen["auth"] == "Bearer sk-or-test"
    body = seen["body"]
    assert body["model"] == "kwaivgi/kling-v3.0-std" and body["prompt"] == "walks forward"
    assert body["duration"] == 5
    assert body["frame_images"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}, "frame_type": "first_frame"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBB"}, "frame_type": "last_frame"},
    ]
    assert body["input_references"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,CCC"}},
        {"type": "audio_url", "audio_url": {"url": "data:audio/mpeg;base64,DDD"}},
    ]
    assert body["provider"] == {"options": {"heygen": {"voice_id": "v1"}}}


async def test_submit_video_omits_empty_prompt_and_unset_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, json={"id": "gen-vid-2"})

    await make_client(handler).submit_video(prompt="", model="heygen/avatar-iv",
                                            input_references=["data:image/png;base64,AAA"])
    assert set(seen["body"]) == {"model", "input_references"}


async def test_generate_images_wraps_references_as_image_parts():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"b64_json": "aGVsbG8=", "media_type": "image/png"}],
                                         "usage": {"cost": 0.01}})

    media = await make_client(handler).generate_images(
        prompt="a fox", model="google/gemini-2.5-flash-image",
        input_references=["data:image/png;base64,AAA"],
    )
    assert seen["body"]["input_references"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]
    assert media[0].data == b"hello" and media[0].cost == 0.01


async def test_video_error_is_a_provider_error_with_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "frame_images invalid"}})

    with pytest.raises(ProviderError, match="frame_images invalid"):
        await make_client(handler).submit_video(prompt="x", model="m")
