"""Shared fixtures: synthetic media made with ffmpeg's lavfi sources (no
network, no keys) and helpers to put files into the asset library."""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# One data dir for the whole session, set before any engine module reads it.
os.environ.setdefault("CHAMP_DATA_DIR", tempfile.mkdtemp(prefix="champstudio-test-"))
os.environ["CHAMP_OFFLINE"] = "1"  # never hit the network from tests

from champstudio import library  # noqa: E402
from champstudio.render import media  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not media.available(), reason="ffmpeg not installed")


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def media_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="champ-media-"))
    return d


@pytest.fixture(scope="session")
def color_video(media_dir) -> str:
    """2 s, 30 fps, 320x180: red for 1 s then blue for 1 s, with a 440 Hz tone."""
    out = media_dir / "redblue.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=1",
            "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "1",
            "-c:a", "aac", str(out))
    return str(out)


@pytest.fixture(scope="session")
def tone(media_dir) -> str:
    out = media_dir / "tone.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:duration=3", "-ac", "2", str(out))
    return str(out)


@pytest.fixture(scope="session")
def speechlike(media_dir) -> str:
    """1 s tone, 1 s silence, 1 s tone — for silence detection."""
    out = media_dir / "speechlike.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:duration=1",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1",
            "-f", "lavfi", "-i", "sine=frequency=300:duration=1",
            "-filter_complex", "[0:a]aformat=sample_rates=44100:channel_layouts=mono[a];"
                               "[2:a]aformat=sample_rates=44100:channel_layouts=mono[c];"
                               "[a][1:a][c]concat=n=3:v=0:a=1[o]",
            "-map", "[o]", str(out))
    return str(out)


def add_to_library(path: str, mime: str) -> str:
    return library.save_asset(Path(path).read_bytes(), mime, tool="test")["id"]
