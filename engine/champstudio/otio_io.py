"""OpenTimelineIO interchange: our Timeline <-> OTIO, and through OTIO's
adapters Premiere XML (fcp_xml) and CMX 3600 EDL.

Cuts, tracks, gaps, transitions, markers and speed survive the trip into
Premiere/Resolve/FCP; our titles, keyframes, colour and captions travel in
`metadata["champ"]` so a round trip through OTIO is lossless for us while
other NLEs see plain clips (the industry norm — effects don't interchange).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import opentimelineio as otio

from . import library
from .timeline import Clip, Marker, Timeline, Track

FORMATS = {
    "otio": {"adapter": "otio_json", "ext": ".otio", "mime": "application/json"},
    "fcp_xml": {"adapter": "fcp_xml", "ext": ".xml", "mime": "application/xml",
                "label": "Premiere Pro / FCP7 XML"},
    "edl": {"adapter": "cmx_3600", "ext": ".edl", "mime": "text/plain", "label": "CMX 3600 EDL"},
}


def available_formats() -> list[str]:
    try:
        names = set(otio.adapters.available_adapter_names())
    except Exception:  # noqa: BLE001
        names = {"otio_json"}
    return [k for k, v in FORMATS.items() if v["adapter"] in names]


def _rt(frames: float, fps: int) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(frames, fps)


def _lanes(clips: list[Clip]) -> list[list[Clip]]:
    """Split possibly-overlapping clips into non-overlapping lanes."""
    lanes: list[list[Clip]] = []
    for c in sorted(clips, key=lambda c: (c.start, c.id)):
        for lane in lanes:
            if lane[-1].end <= c.start:
                lane.append(c)
                break
        else:
            lanes.append([c])
    return lanes


def _media_ref(clip: Clip, fps: int, *, media_prefix: str | None) -> otio.schema.MediaReference:
    # NLE adapters (fcp_xml) need every reference to carry its available range
    span = otio.opentime.TimeRange(_rt(0, fps), _rt(clip.in_ + max(1, int(clip.duration * clip.speed)), fps))
    if clip.asset_id:
        asset = library.get_asset(clip.asset_id)
        if asset:
            target = f"{media_prefix}{asset['filename']}" if media_prefix is not None else asset["url"]
            p = library.asset_path(clip.asset_id)
            try:
                from .render import media

                dur = media.probe(str(p)).get("duration") or 0
                if dur:
                    span = otio.opentime.TimeRange(_rt(0, fps), _rt(max(span.duration.value, round(dur * fps)), fps))
            except Exception:  # noqa: BLE001 — stills / unreadable media keep the clip span
                pass
            ref = otio.schema.ExternalReference(target_url=target, available_range=span)
            ref.name = asset["filename"]
            return ref
    if clip.type in ("text", "shape", "color", "captions", "adjustment"):
        return otio.schema.GeneratorReference(name=clip.type, generator_kind=f"champ.{clip.type}",
                                              available_range=span)
    return otio.schema.MissingReference(available_range=span)


def to_otio(tl: Timeline, *, name: str = "Champ Studio", media_prefix: str | None = None) -> otio.schema.Timeline:
    fps = tl.fps
    out = otio.schema.Timeline(name=name)
    out.global_start_time = _rt(0, fps)
    out.metadata["champ"] = {"version": tl.version, "fps": fps, "width": tl.width, "height": tl.height,
                             "background": tl.background, "loudness": tl.loudness, "meta": tl.meta}
    for track in tl.tracks:
        kind = otio.schema.TrackKind.Video if track.kind == "video" else otio.schema.TrackKind.Audio
        lanes = _lanes([c for c in track.clips]) or [[]]
        for li, lane in enumerate(lanes):
            ot = otio.schema.Track(name=track.name or track.id if li == 0 else f"{track.name or track.id} ({li + 1})",
                                   kind=kind)
            ot.metadata["champ"] = {"track": track.model_dump(exclude={"clips"}), "lane": li}
            cursor = 0
            for c in lane:
                if c.start > cursor:
                    ot.append(otio.schema.Gap(source_range=otio.opentime.TimeRange(_rt(0, fps), _rt(c.start - cursor, fps))))
                if c.transition_in and c.transition_in.duration > 0 and len(ot) and isinstance(ot[-1], otio.schema.Clip):
                    tr = otio.schema.Transition(
                        name=c.transition_in.type,
                        transition_type=otio.schema.TransitionTypes.SMPTE_Dissolve,
                        in_offset=_rt(0, fps), out_offset=_rt(c.transition_in.duration, fps))
                    tr.metadata["champ"] = {"type": c.transition_in.type}
                    ot.append(tr)
                oc = otio.schema.Clip(
                    name=c.name or c.text[:40] or c.type,
                    media_reference=_media_ref(c, fps, media_prefix=media_prefix),
                    source_range=otio.opentime.TimeRange(_rt(c.in_, fps), _rt(c.duration, fps)),
                )
                if c.speed != 1.0:
                    oc.effects.append(otio.schema.LinearTimeWarp(time_scalar=c.speed))
                oc.metadata["champ"] = c.model_dump(by_alias=True)
                ot.append(oc)
                cursor = c.end
            out.tracks.append(ot)
    for m in tl.markers:
        mk = otio.schema.Marker(name=m.label, marked_range=otio.opentime.TimeRange(_rt(m.f, fps), _rt(m.duration, fps)))
        mk.metadata["champ"] = m.model_dump()
        out.tracks.markers.append(mk)
    return out


def from_otio(ot: otio.schema.Timeline, *, fps: int | None = None, width: int = 1920, height: int = 1080) -> Timeline:
    meta = dict(ot.metadata.get("champ") or {})
    rate = fps or meta.get("fps")
    if not rate:
        try:
            rate = int(round(ot.duration().rate)) or 30
        except Exception:  # noqa: BLE001
            rate = 30
    rate = int(rate)
    tracks: dict[str, Track] = {}
    order: list[str] = []
    for ti, ot_track in enumerate(ot.tracks):
        tmeta = dict((ot_track.metadata.get("champ") or {}).get("track") or {})
        tid = tmeta.get("id") or f"{'v' if ot_track.kind == otio.schema.TrackKind.Video else 'a'}{ti + 1}"
        if tid not in tracks:
            kind = "video" if ot_track.kind == otio.schema.TrackKind.Video else "audio"
            tracks[tid] = Track(**{**tmeta, "id": tid, "kind": kind, "name": tmeta.get("name") or ot_track.name,
                                   "clips": []})
            order.append(tid)
        track = tracks[tid]
        pending_transition = None
        for idx, item in enumerate(ot_track):
            if isinstance(item, otio.schema.Transition):
                pending_transition = item
                continue
            if not isinstance(item, otio.schema.Clip):
                continue
            rng = ot_track.range_of_child_at_index(idx)
            start = int(round(rng.start_time.rescaled_to(rate).value))
            dur = int(round(rng.duration.rescaled_to(rate).value))
            cm = item.metadata.get("champ")
            if cm:
                clip = Clip(**dict(cm))
                clip.start, clip.duration = start, dur
            else:
                src = item.source_range or item.available_range()
                clip = Clip(
                    id=f"c{ti}_{idx}",
                    type="video" if track.kind == "video" else "audio",
                    start=start, duration=dur, name=item.name,
                    **{"in": int(round(src.start_time.rescaled_to(rate).value)) if src else 0},
                )
                ref = item.media_reference
                if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
                    aid = _asset_id_from_url(ref.target_url)
                    if aid:
                        clip.asset_id = aid
                    else:
                        clip.source = {"offline": True, "media": Path(ref.target_url).name, "url": ref.target_url}
                for eff in item.effects:
                    if isinstance(eff, otio.schema.LinearTimeWarp) and eff.time_scalar:
                        clip.speed = float(eff.time_scalar)
                if pending_transition is not None:
                    d = pending_transition.in_offset + pending_transition.out_offset
                    clip.transition_in = {"type": "crossfade",
                                          "duration": max(1, int(round(d.rescaled_to(rate).value)))}
            pending_transition = None
            track.clips.append(clip)
    markers = []
    for m in ot.tracks.markers:
        mm = m.metadata.get("champ")
        if mm:
            markers.append(Marker(**dict(mm)))
        else:
            markers.append(Marker(f=int(round(m.marked_range.start_time.rescaled_to(rate).value)), label=m.name))
    return Timeline(fps=rate, width=meta.get("width", width), height=meta.get("height", height),
                    background=meta.get("background", "#000000"), loudness=meta.get("loudness", -14.0),
                    meta=meta.get("meta") or {}, tracks=[tracks[t] for t in order], markers=markers)


def _asset_id_from_url(url: str) -> str | None:
    if "/api/assets/" in url:
        part = url.split("/api/assets/", 1)[1].split("/", 1)[0]
        return part if library.get_asset(part) else None
    stem = Path(url).stem
    return stem if library.get_asset(stem) else None


def export_string(tl: Timeline, fmt: str, *, name: str = "Champ Studio", media_prefix: str | None = None) -> str:
    spec = FORMATS[fmt]
    ot = to_otio(tl, name=name, media_prefix=media_prefix)
    if fmt in ("fcp_xml", "edl"):
        # NLEs can't use our generated titles/shapes/captions: keep their timing as gaps
        for track in ot.tracks:
            for i, item in enumerate(list(track)):
                if isinstance(item, otio.schema.Clip) and not isinstance(item.media_reference,
                                                                          otio.schema.ExternalReference):
                    track[i] = otio.schema.Gap(source_range=item.source_range)
    if fmt == "edl":  # EDL is single video track only
        video = [t for t in ot.tracks if t.kind == otio.schema.TrackKind.Video][:1]
        ot = otio.schema.Timeline(name=name, tracks=[t.deepcopy() for t in video])
        ot.global_start_time = _rt(0, tl.fps)
    return otio.adapters.write_to_string(ot, spec["adapter"])


def import_string(data: str, fmt: str, **kw) -> Timeline:
    ot = otio.adapters.read_from_string(data, FORMATS[fmt]["adapter"])
    if isinstance(ot, otio.schema.SerializableCollection):
        ot = next(iter(ot))
    return from_otio(ot, **kw)


def export_package(tl: Timeline, fmt: str, *, name: str = "Champ Studio") -> bytes:
    """Zip with the interchange file plus every referenced media file under media/."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{_safe(name)}{FORMATS[fmt]['ext']}", export_string(tl, fmt, name=name, media_prefix="media/"))
        added = set()
        for t in tl.tracks:
            for c in t.clips:
                if c.asset_id and c.asset_id not in added:
                    p = library.asset_path(c.asset_id)
                    a = library.get_asset(c.asset_id)
                    if p and a:
                        z.write(p, f"media/{a['filename']}")
                        added.add(c.asset_id)
    return buf.getvalue()


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in name).strip() or "project"
