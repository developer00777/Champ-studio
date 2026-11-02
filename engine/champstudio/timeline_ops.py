"""Pure timeline edits used by the AI tools (server side twins of the editor's ops).

The key invariant (a bug in the old Edit Studio): after cutting, every later
step works in *timeline* time through an explicit old→new frame mapping —
captions and markers are re-derived through `FrameMap`, never from raw
source timestamps.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .timeline import Clip, Timeline


@dataclass
class FrameMap:
    """Maps old timeline frames to new ones after ripple deletions."""
    removed: list[tuple[int, int]] = field(default_factory=list)  # [start, end) in OLD frames, sorted

    def add(self, start: int, end: int) -> None:
        if end > start:
            self.removed.append((start, end))
            self.removed.sort()

    def __call__(self, f: int) -> int | None:
        shift = 0
        for s, e in self.removed:
            if f >= e:
                shift += e - s
            elif f >= s:
                return None
        return f - shift

    def clamp(self, f: int) -> int:
        """Like __call__ but frames inside a removed range snap to the cut point."""
        shift = 0
        for s, e in self.removed:
            if f >= e:
                shift += e - s
            elif f >= s:
                return s - shift
        return f - shift


def linked_group(tl: Timeline, clip_id: str) -> list[Clip]:
    root = tl.clip(clip_id)
    if root is None:
        raise KeyError(clip_id)
    ids = {root.id}
    keys = {root.id} | ({root.linked} if root.linked else set())
    for t in tl.tracks:
        for c in t.clips:
            if c.id in keys or (c.linked and c.linked in keys):
                ids.add(c.id)
    return [c for t in tl.tracks for c in t.clips if c.id in ids]


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for s, e in sorted(ranges):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def remove_source_ranges(tl: Timeline, clip_id: str, ranges_s: list[tuple[float, float]],
                         *, min_keep_frames: int = 2) -> tuple[Timeline, FrameMap]:
    """Ripple-delete source time ranges (seconds, in the clip's media) from a clip
    and everything linked to it. Later clips on every track move left; captions
    and markers are re-timed through the returned FrameMap."""
    tl = tl.model_copy(deep=True)
    fps = tl.fps
    root = tl.clip(clip_id)
    if root is None:
        raise KeyError(clip_id)
    # convert source seconds → timeline frames of the root clip
    cut_tl: list[tuple[int, int]] = []
    src0 = root.in_
    src1 = root.in_ + int(round(root.duration * root.speed))
    for a, b in ranges_s:
        fa, fb = int(round(a * fps)), int(round(b * fps))
        fa, fb = max(fa, src0), min(fb, src1)
        if fb - fa < 1:
            continue
        ta = root.start + int(round((fa - src0) / root.speed))
        tb = root.start + int(round((fb - src0) / root.speed))
        cut_tl.append((ta, tb))
    cut_tl = _merge(cut_tl)
    fmap = FrameMap()
    if not cut_tl:
        return tl, fmap
    for s, e in cut_tl:
        fmap.add(s, e)

    group = {c.id for c in linked_group(tl, clip_id)}
    for track in tl.tracks:
        new_clips: list[Clip] = []
        for c in track.clips:
            if c.id in group:
                new_clips.extend(_cut_clip(c, cut_tl, fmap, min_keep_frames))
            elif c.type == "captions" and c.captions:
                new_clips.append(_retime_captions(c, fmap))
            else:
                new_clips.append(_shift_clip(c, fmap))
        track.clips = new_clips
    for m in tl.markers:
        m.f = fmap.clamp(m.f)
    return tl, fmap


def _cut_clip(c: Clip, cuts: list[tuple[int, int]], fmap: FrameMap, min_keep: int) -> list[Clip]:
    """Split one clip into its kept pieces (timeline frames), re-placed via fmap."""
    pieces: list[tuple[int, int]] = []
    cur = c.start
    for s, e in cuts:
        if e <= c.start or s >= c.end:
            continue
        if s > cur:
            pieces.append((cur, min(s, c.end)))
        cur = max(cur, e)
    if cur < c.end:
        pieces.append((cur, c.end))
    out = []
    for n, (a, b) in enumerate(p for p in pieces if p[1] - p[0] >= min_keep):
        nc = c.model_copy(deep=True)
        nc.id = c.id if n == 0 else f"{c.id}-k{n}"
        nc.in_ = c.in_ + int(round((a - c.start) * c.speed))
        nc.start = fmap.clamp(a)
        nc.duration = b - a
        if n > 0:
            nc.transition_in = None
            nc.anim_in = None
        nc.keyframes = {}  # source-relative animation no longer lines up after cuts
        if c.type == "audio":  # tiny fades at cut points avoid clicks
            nc.fade_in = max(nc.fade_in if n == 0 else 0, 1 if a != c.start else 0)
            nc.fade_out = max(nc.fade_out if b == c.end else 0, 1 if b != c.end else 0)
        if c.linked:
            nc.linked = c.linked if n == 0 else f"{c.linked}-k{n}"
        elif n > 0:
            nc.linked = None
        out.append(nc)
    # keep link pairs consistent: picture piece n links to audio piece n
    return out


def _shift_clip(c: Clip, fmap: FrameMap) -> Clip:
    new_start = fmap.clamp(c.start)
    new_end = fmap.clamp(c.end)
    nc = c.model_copy(deep=True)
    nc.start = new_start
    nc.duration = max(1, new_end - new_start)
    return nc


def _retime_captions(c: Clip, fmap: FrameMap) -> Clip:
    nc = _shift_clip(c, fmap)
    words = []
    for w in c.captions.words:
        s, e = fmap(w.s), fmap(max(w.s, w.e - 1))
        if s is None or e is None:
            continue
        words.append(w.model_copy(update={"s": s, "e": e + 1}))
    nc.captions = c.captions.model_copy(update={"words": words})
    return nc


def split_clip(tl: Timeline, clip_id: str, frame: int) -> Timeline:
    tl = tl.model_copy(deep=True)
    for t in tl.tracks:
        for i, c in enumerate(t.clips):
            if c.id == clip_id and c.start < frame < c.end:
                right = c.model_copy(deep=True)
                cut = frame - c.start
                right.id = f"{c.id}-s{frame}"
                right.start = frame
                right.duration = c.duration - cut
                right.in_ = c.in_ + int(round(cut * c.speed))
                right.transition_in = None
                right.anim_in = None
                c.duration = cut
                c.anim_out = None
                t.clips.insert(i + 1, right)
                return tl
    return tl


def source_words_to_timeline(tl: Timeline, clip: Clip, words: list[dict]) -> list[dict]:
    """Transcript words (source seconds) → caption words (timeline frames) for one clip."""
    fps = tl.fps
    out = []
    src0 = clip.in_
    src1 = clip.in_ + clip.duration * clip.speed
    for w in words:
        sf, ef = w["start"] * fps, w["end"] * fps
        if ef <= src0 or sf >= src1:
            continue
        s = clip.start + int(round((max(sf, src0) - src0) / clip.speed))
        e = clip.start + int(round((min(ef, src1) - src0) / clip.speed))
        out.append({"w": w["word"], "s": s, "e": max(e, s + 1)})
    return out


def summarize(tl: Timeline, max_clips: int = 200) -> dict:
    """Compact view of a timeline for LLM prompts."""
    clips = []
    for t in tl.tracks:
        for c in t.clips[:max_clips]:
            row = {"id": c.id, "track": t.id, "type": c.type, "start": c.start, "duration": c.duration}
            if c.name:
                row["name"] = c.name
            if c.text:
                row["text"] = c.text[:80]
            if c.role:
                row["role"] = c.role
            clips.append(row)
    return {"fps": tl.fps, "width": tl.width, "height": tl.height, "duration": tl.duration,
            "tracks": [{"id": t.id, "kind": t.kind, "name": t.name} for t in tl.tracks],
            "clips": clips, "markers": [m.model_dump() for m in tl.markers]}


def next_free_video_track(tl: Timeline, start: int, end: int, name: str = "") -> str:
    """Topmost video track with nothing in [start, end), adding one when needed."""
    for t in reversed(tl.video_tracks()):
        if t.locked:
            continue
        if all(c.end <= start or c.start >= end for c in t.clips):
            return t.id
    n = len(tl.video_tracks()) + 1
    tid = f"v{n}"
    while any(t.id == tid for t in tl.tracks):
        n += 1
        tid = f"v{n}"
    idx = max((i for i, t in enumerate(tl.tracks) if t.kind == "video"), default=-1) + 1
    from .timeline import Track

    tl.tracks.insert(idx, Track(id=tid, kind="video", name=name or f"V{n}"))
    return tid


def track(tl: Timeline, tid: str):
    return next(t for t in tl.tracks if t.id == tid)


def deep(x):
    return copy.deepcopy(x)
