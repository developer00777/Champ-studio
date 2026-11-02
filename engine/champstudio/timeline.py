"""The one timeline model shared by templates, Director and the editor.

Time is integer frames at `Timeline.fps`; clip `start`/`duration`/`in` and
keyframe `f` (relative to clip start) are frames. Geometry is normalized to
the canvas: `box = [x, y, w, h]` in 0..1, transform `x`/`y` are offsets in
canvas units. Video tracks composite in list order (first = bottom, like V1).
Video clips never carry sound — sound is always an audio clip (which may point
at a video asset), linked to its picture by `linked`.

OpenTimelineIO is the interchange format (see `otio_io.py`); this model is
what the renderer and editor actually evaluate.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Ease = Literal["linear", "ease", "ease-in", "ease-out", "hold"]


class Keyframe(BaseModel):
    f: int                      # frame relative to clip start
    v: float
    ease: Ease = "ease"         # easing from this keyframe to the next


class Anim(BaseModel):
    type: str                   # fade | slide-up | slide-down | slide-left | slide-right | pop | zoom-in
                                # | zoom-out | wipe | typewriter | blur  (in/out)
                                # transitions: crossfade | dip-black | dip-white | wipe-left | wipe-right
                                # | slide-left | slide-right | slide-up | zoom
    duration: int = 12          # frames


class Transform(BaseModel):
    x: float = 0.0              # offset, canvas widths
    y: float = 0.0              # offset, canvas heights
    scale: float = 1.0
    rotation: float = 0.0       # degrees, clockwise
    opacity: float = 1.0


class TextStyle(BaseModel):
    model_config = ConfigDict(extra="allow")

    font: str = "Inter"
    weight: int = 700
    size: float = 64            # px at 1080 canvas height (scaled to output)
    min_size: float | None = None
    color: str = "#FFFFFF"
    align: Literal["left", "center", "right"] = "center"
    valign: Literal["top", "middle", "bottom"] = "middle"
    line_height: float = 1.15
    letter_spacing: float = 0.0
    uppercase: bool = False
    fit: Literal["shrink", "none"] = "shrink"
    stroke_color: str | None = None
    stroke_width: float = 0
    shadow: bool = False
    shadow_color: str = "#000000AA"
    bg_color: str | None = None     # pill/box behind the text
    bg_padding: float = 0.35        # em
    bg_radius: float = 0.25         # em
    highlight_color: str | None = None   # captions: active word colour
    highlight_bg: str | None = None      # captions: active word box


class Shape(BaseModel):
    kind: Literal["rect", "ellipse", "line", "triangle", "polygon", "star"] = "rect"
    fill: str | None = "#FFFFFF"
    radius: float = 0.0          # fraction of min(w, h)
    stroke: str | None = None
    stroke_width: float = 0
    gradient: list[str] | None = None   # [start, end] colours
    gradient_angle: float = 180.0       # degrees; 180 = top → bottom, 90 = left → right
    sides: int = 6                      # polygon sides / star points


class Adjust(BaseModel):
    """Basic colour (Lumetri Basic equivalent)."""
    exposure: float = 0.0        # stops
    contrast: float = 0.0        # -1..1
    saturation: float = 1.0      # 0..2
    temperature: float = 0.0     # -1 (cool) .. 1 (warm)
    tint: float = 0.0            # -1 (green) .. 1 (magenta)
    brightness: float = 0.0      # -1..1
    lut_asset_id: str | None = None
    lut_strength: float = 1.0


class CaptionWord(BaseModel):
    w: str
    s: int                       # timeline frames (absolute)
    e: int


class Captions(BaseModel):
    words: list[CaptionWord] = []
    mode: Literal["line", "karaoke", "word"] = "karaoke"
    max_chars: int = 32
    max_lines: int = 2


class TextAnim(BaseModel):
    """Per-unit text animator (After Effects / Essential Graphics style): each
    character, word or line animates in on its own, offset by `stagger`."""
    by: Literal["char", "word", "line"] = "word"
    type: Literal["fade", "rise", "drop", "pop", "slide-left", "slide-right", "reveal"] = "rise"
    duration: int = 10          # frames per unit
    stagger: int = 2            # frames between units
    delay: int = 0              # frames after the clip starts
    order: Literal["forward", "reverse", "center", "random"] = "forward"
    out: bool = False           # also animate out (mirrored) at the end


class Counter(BaseModel):
    """Count the first number in the text up from `start` (e.g. "$4.2M", "87%")."""
    start: float = 0.0
    duration: int = 30          # frames
    delay: int = 0
    ease: Ease = "ease-out"


class Pin(BaseModel):
    """Responsive position: keep this layer's box wrapped around another layer's
    content (e.g. a bar that grows with the name typed into a lower third)."""
    to: str                     # layer id inside the same graphic
    pad: list[float] = Field(default_factory=lambda: [0.02, 0.012, 0.02, 0.012])  # l, t, r, b (canvas units)
    sides: list[Literal["left", "top", "right", "bottom"]] = Field(
        default_factory=lambda: ["left", "top", "right", "bottom"])


class Control(BaseModel):
    """An exposed "master property" of a graphic (MOGRT-style). Layers read it
    through `bind` paths such as {"text": "{{name}}", "style.color": "{{accent}}"}."""
    model_config = ConfigDict(extra="allow")

    id: str
    label: str = ""
    kind: Literal["text", "color", "number", "toggle", "choice", "media"] = "text"
    value: bool | int | float | str | dict | None = ""
    options: list[str] = []
    min: float | None = None
    max: float | None = None
    multiline: bool = False


class Graphic(BaseModel):
    """A graphic clip (Premiere's Essential Graphics): a stack of layers, bottom
    first. Each layer is a Clip (text/shape/image/color) whose start/duration are
    relative to the graphic clip; `hold` layers run to the graphic's end, so
    stretching the clip keeps intro and outro animations intact."""
    layers: list["Clip"] = []
    controls: list[Control] = []
    template: str | None = None


class Clip(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str
    type: Literal["video", "image", "text", "shape", "color", "audio", "captions", "adjustment", "graphic"]
    start: int = 0
    duration: int = 30
    in_: int = Field(0, alias="in")         # source offset, frames
    asset_id: str | None = None
    src: str | None = None                  # engine-internal file (must live in the data dir)
    name: str = ""
    enabled: bool = True
    box: list[float] = Field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])
    fit: Literal["cover", "contain", "fill", "reframe"] = "cover"
    transform: Transform = Field(default_factory=Transform)
    keyframes: dict[str, list[Keyframe]] = {}
    anim_in: Anim | None = None
    anim_out: Anim | None = None
    transition_in: Anim | None = None
    speed: float = 1.0
    reverse: bool = False
    freeze: bool = False                    # hold the in-point frame
    # content
    text: str = ""
    style: TextStyle = Field(default_factory=TextStyle)
    shape: Shape = Field(default_factory=Shape)
    color: str = "#000000"
    adjust: Adjust | None = None
    captions: Captions | None = None
    # motion graphics (any text clip, and layers inside a graphic)
    graphic: Graphic | None = None
    text_anim: TextAnim | None = None
    counter: Counter | None = None
    pin: Pin | None = None                  # graphic layers only
    mask: Literal["below", "below-inverse"] | None = None   # graphic layers: clip to the layers beneath
    bind: dict[str, str] = {}               # graphic layers: property path → "{{control}}" template
    hold: bool = True                       # graphic layers: run to the end of the graphic
    locked: bool = False                    # graphic layers: not selectable on the monitor
    # audio
    volume: float = 1.0                     # linear gain
    fade_in: int = 0                        # frames
    fade_out: int = 0
    role: Literal["dialogue", "vo", "music", "sfx", "ambience"] | None = None
    duck_db: float | None = None            # music: duck under dialogue/vo by this many dB
    # bookkeeping
    linked: str | None = None
    source: dict = {}                       # template/scene/slot back-reference
    nest: dict | None = None                # nested sequence (a Timeline dict)

    @property
    def end(self) -> int:
        return self.start + self.duration

    @model_validator(mode="after")
    def _sane(self):
        if self.duration < 1:
            self.duration = 1
        if self.speed <= 0:
            self.speed = 1.0
        return self


Graphic.model_rebuild()


class Track(BaseModel):
    id: str
    kind: Literal["video", "audio"]
    name: str = ""
    clips: list[Clip] = []
    muted: bool = False
    hidden: bool = False
    locked: bool = False
    solo: bool = False
    volume: float = 1.0


class Marker(BaseModel):
    f: int
    label: str = ""
    color: str = "#FF6B35"
    duration: int = 0


class Timeline(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: int = 1
    fps: int = 30
    width: int = 1920
    height: int = 1080
    background: str = "#000000"
    tracks: list[Track] = []
    markers: list[Marker] = []
    meta: dict = {}
    loudness: float | None = -14.0           # LUFS target for export; None = off

    @property
    def duration(self) -> int:
        ends = [c.end for t in self.tracks for c in t.clips if c.enabled]
        return max(ends, default=0)

    def video_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.kind == "video"]

    def audio_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.kind == "audio"]

    def clip(self, clip_id: str) -> Clip | None:
        for t in self.tracks:
            for c in t.clips:
                if c.id == clip_id:
                    return c
        return None


# ── keyframes / easing ───────────────────────────────────────────────


def ease(t: float, kind: str) -> float:
    t = min(1.0, max(0.0, t))
    if kind == "linear":
        return t
    if kind == "hold":
        return 0.0
    if kind == "ease-in":
        return t * t * t
    if kind == "ease-out":
        return 1 - (1 - t) ** 3
    return t * t * (3 - 2 * t)  # ease (smoothstep)


def eval_keyframes(kfs: list[Keyframe], f: float, default: float) -> float:
    if not kfs:
        return default
    kfs = sorted(kfs, key=lambda k: k.f)
    if f <= kfs[0].f:
        return kfs[0].v
    if f >= kfs[-1].f:
        return kfs[-1].v
    for a, b in zip(kfs, kfs[1:]):
        if a.f <= f <= b.f:
            span = max(1, b.f - a.f)
            return a.v + (b.v - a.v) * ease((f - a.f) / span, a.ease)
    return default


def prop(clip: Clip, name: str, f_rel: float, default: float) -> float:
    """Animated property value at a clip-relative frame."""
    return eval_keyframes(clip.keyframes.get(name, []), f_rel, default)


# ── helpers ──────────────────────────────────────────────────────────

ASPECTS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "4:3": (1440, 1080),
}


def canvas_for(aspect: str, height: int | None = None) -> tuple[int, int]:
    w, h = ASPECTS.get(aspect, ASPECTS["16:9"])
    if height and height != h:
        scale = height / h
        w, h = int(round(w * scale / 2) * 2), int(round(h * scale / 2) * 2)
    return w, h


def seconds_to_frames(sec: float, fps: int) -> int:
    return int(math.floor(sec * fps + 0.5))


def frames_to_seconds(frames: int, fps: int) -> float:
    return frames / fps


def timecode(frames: int, fps: int) -> str:
    s, f = divmod(frames, fps)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"
