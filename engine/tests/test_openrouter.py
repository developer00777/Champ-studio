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


async def test_list_speech_models_exposes_voices():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/models"
        assert request.url.params["output_modalities"] == "speech"
        return httpx.Response(200, json={"data": [
            {"id": "google/gemini-3.8-flash-tts", "name": "Gemini 3.8 Flash TTS",
             "supported_voices": ["Kore", "Puck"], "pricing": {"prompt": "0.0000005"}},
            {"id": "fish-audio/s2-pro", "name": "Fish S2 Pro"},
        ]})

    models = await make_client(handler).list_speech_models()
    assert models[0] == {
        "id": "google/gemini-3.8-flash-tts", "name": "Gemini 3.8 Flash TTS",
        "provider": "openrouter", "vendor": "google", "voices": ["Kore", "Puck"],
        "meta": {"pricing": {"prompt": "0.0000005"}},
    }
    assert models[1]["voices"] == [] and models[1]["vendor"] == "fish-audio"


async def test_synthesize_speech_posts_mp3_request_and_returns_audio_media():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3fake", headers={
            "content-type": "audio/mpeg", "x-generation-id": "gen-tts-1"})

    media = await make_client(handler).synthesize_speech(
        text="Hello team", model="google/gemini-3.8-flash-tts", voice="Kore", speed=1.2)
    assert seen["url"] == "https://openrouter.ai/api/v1/audio/speech"
    assert seen["body"] == {"model": "google/gemini-3.8-flash-tts", "input": "Hello team",
                            "voice": "Kore", "response_format": "mp3", "speed": 1.2}
    assert media.kind == "audio" and media.mime == "audio/mpeg" and media.data == b"ID3fake"
    assert media.model == "google/gemini-3.8-flash-tts" and media.prompt == "Hello team"
    assert media.meta["generation_id"] == "gen-tts-1"


async def test_synthesize_speech_omits_voice_and_default_speed():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3", headers={"content-type": "audio/mpeg"})

    await make_client(handler).synthesize_speech(text="x", model="m", speed=1.0)
    assert seen["body"] == {"model": "m", "input": "x", "response_format": "mp3"}


async def test_synthesize_speech_errors_surface_provider_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "voice is required"}})

    with pytest.raises(ProviderError, match="voice is required"):
        await make_client(handler).synthesize_speech(text="x", model="m")


async def test_synthesize_speech_rejects_empty_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"content-type": "audio/mpeg"})

    with pytest.raises(ProviderError, match="empty audio"):
        await make_client(handler).synthesize_speech(text="x", model="m")
