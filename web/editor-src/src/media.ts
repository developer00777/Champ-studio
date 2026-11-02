// Asset + derived-media cache: ingest status (proxies, thumbnail sprites,
// waveform peaks), image elements, and server fonts registered as FontFaces.

import { ApiError, get, post, type Asset, type FontFile, type MediaStatus } from "./api";
import { Emitter } from "./store";

export interface Peaks {
  per_second: number;
  peaks: number[];
}

type MediaEvent = "media" | "assets" | "fonts";

class MediaCache extends Emitter<MediaEvent> {
  assets = new Map<string, Asset>();
  status = new Map<string, MediaStatus>();
  thumbs = new Map<string, HTMLImageElement>();
  peaks = new Map<string, Peaks>();
  images = new Map<string, HTMLImageElement>();
  private pending = new Map<string, Promise<MediaStatus | null>>();
  private polling = new Set<string>();
  private ingestRequested = new Set<string>();
  /** Assets whose proxies are still being built. */
  building = new Set<string>();

  async loadAssets(): Promise<Asset[]> {
    const res = await get<{ assets: Asset[] }>("/api/assets?limit=500");
    for (const a of res.assets) this.assets.set(a.id, a);
    this.emit("assets");
    return res.assets;
  }

  async asset(id: string): Promise<Asset | null> {
    const a = this.assets.get(id);
    if (a) return a;
    try {
      const got = await get<Asset>(`/api/assets/${id}`);
      this.assets.set(id, got);
      return got;
    } catch {
      return null;
    }
  }

  addAsset(a: Asset): void {
    this.assets.set(a.id, a);
    this.emit("assets");
  }

  fileUrl(id: string): string {
    return `/api/assets/${id}/file`;
  }

  /** Best URL for realtime playback: the proxy if built, else the original. */
  playUrl(id: string): string {
    return this.status.get(id)?.proxy ?? this.fileUrl(id);
  }

  /** Ensure media status is known; trigger ingest for video/audio that isn't ready. */
  ensure(id: string): Promise<MediaStatus | null> {
    const known = this.status.get(id);
    if (known?.ready) return Promise.resolve(known);
    let p = this.pending.get(id);
    if (p) return p;
    p = this.fetchStatus(id).finally(() => this.pending.delete(id));
    this.pending.set(id, p);
    return p;
  }

  /** Re-read the media status (e.g. after a transcript was generated). */
  async refresh(id: string): Promise<MediaStatus | null> {
    try {
      const st = await get<MediaStatus>(`/api/assets/${id}/media`);
      this.setStatus(id, st);
      return st;
    } catch {
      return this.status.get(id) ?? null;
    }
  }

  private async fetchStatus(id: string): Promise<MediaStatus | null> {
    let st: MediaStatus;
    try {
      st = await get<MediaStatus>(`/api/assets/${id}/media`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
    this.setStatus(id, st);
    if (!st.ready && !this.ingestRequested.has(id)) {
      this.ingestRequested.add(id);
      try {
        const r = await post<{ status: MediaStatus; job_id: string | null }>(`/api/assets/${id}/ingest`);
        if (r.status) this.setStatus(id, r.status);
      } catch {
        /* ingest unavailable — the original file still plays */
      }
      if (!this.status.get(id)?.ready) this.poll(id);
    }
    return this.status.get(id) ?? st;
  }

  private poll(id: string): void {
    if (this.polling.has(id)) return;
    this.polling.add(id);
    this.building.add(id);
    this.emit("media");
    let tries = 0;
    const tick = async () => {
      tries++;
      try {
        const st = await get<MediaStatus>(`/api/assets/${id}/media`);
        this.setStatus(id, st);
        if (st.ready || tries > 400) {
          this.polling.delete(id);
          this.building.delete(id);
          this.emit("media");
          return;
        }
      } catch {
        if (tries > 20) {
          this.polling.delete(id);
          this.building.delete(id);
          this.emit("media");
          return;
        }
      }
      setTimeout(tick, Math.min(5000, 1200 + tries * 200));
    };
    setTimeout(tick, 1200);
  }

  private setStatus(id: string, st: MediaStatus): void {
    const prev = this.status.get(id);
    this.status.set(id, st);
    if (st.thumbs && !this.thumbs.has(id)) {
      const img = new Image();
      img.decoding = "async";
      img.onload = () => this.emit("media");
      img.src = st.thumbs;
      this.thumbs.set(id, img);
    }
    if (st.peaks && !this.peaks.has(id)) {
      this.peaks.set(id, { per_second: 50, peaks: [] });
      get<Peaks>(st.peaks)
        .then((p) => {
          this.peaks.set(id, p);
          this.emit("media");
        })
        .catch(() => this.peaks.delete(id));
    }
    if (!prev || prev.ready !== st.ready || prev.proxy !== st.proxy) this.emit("media");
  }

  /** Source duration in seconds (from probe), if known. */
  duration(id: string): number | undefined {
    const d = this.status.get(id)?.probe?.duration;
    return d && d > 0 ? d : undefined;
  }

  /** Duration in seconds, falling back to the browser's metadata when the probe isn't ready yet. */
  async durationAsync(id: string): Promise<number | undefined> {
    await Promise.race([this.ensure(id).catch(() => null), new Promise((r) => setTimeout(r, 1500))]);
    const d = this.duration(id);
    if (d) return d;
    const kind = this.assets.get(id)?.kind;
    if (kind !== "video" && kind !== "audio") return undefined;
    return new Promise((resolve) => {
      const el = document.createElement(kind === "audio" ? "audio" : "video");
      el.preload = "metadata";
      const done = (v?: number) => {
        el.removeAttribute("src");
        resolve(v);
      };
      el.onloadedmetadata = () => done(Number.isFinite(el.duration) ? el.duration : undefined);
      el.onerror = () => done(undefined);
      setTimeout(() => done(undefined), 4000);
      el.src = this.playUrl(id);
    });
  }

  image(id: string): HTMLImageElement {
    let img = this.images.get(id);
    if (!img) {
      img = new Image();
      img.decoding = "async";
      img.onload = () => this.emit("media");
      img.src = this.fileUrl(id);
      this.images.set(id, img);
    }
    return img;
  }

  // ── fonts ──

  families: string[] = [];
  private fontMap = new Map<string, string>();

  /** Register every engine font as a FontFace so canvas text matches the server. */
  async loadFonts(): Promise<void> {
    let res: { families: string[]; files: FontFile[] };
    try {
      res = await get("/api/fonts");
    } catch {
      return;
    }
    const weights: Record<string, number> = { Regular: 400, Medium: 500, SemiBold: 600, Bold: 700, ExtraBold: 800, Black: 900 };
    const loads: Promise<unknown>[] = [];
    const fams = new Set<string>();
    for (const f of res.files) {
      const key = normFont(f.family);
      const cssFamily = `CS ${key}`;
      const stem = f.file.replace(/\.[ot]tf$/i, "");
      const style = stem.includes("-") ? stem.split("-").slice(1).join("-") : "";
      const weight = f.variable ? "100 900" : String(weights[style] ?? f.weight ?? 400);
      try {
        const face = new FontFace(cssFamily, `url(${f.url})`, { weight });
        document.fonts.add(face);
        loads.push(face.load().catch(() => undefined));
        this.fontMap.set(key, cssFamily);
        fams.add(prettyFamily(f));
      } catch {
        /* bad font descriptor */
      }
    }
    this.families = [...fams].sort();
    await Promise.all(loads);
    this.emit("fonts");
  }

  /** CSS font-family for a TextStyle.font (falls back like the engine: Inter). */
  cssFamily(font: string): string {
    const k = normFont(font);
    const f = this.fontMap.get(k) ?? this.fontMap.get("inter");
    return f ? `"${f}", Inter, sans-serif` : `"${font}", Inter, sans-serif`;
  }
}

export function normFont(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function prettyFamily(f: FontFile): string {
  // the engine splits CamelCase file stems ("D M Sans"); rebuild from the file stem
  const stem = f.file.replace(/\.[ot]tf$/i, "").split("-")[0]!.split("[")[0]!;
  return stem.replace(/([a-z])([A-Z])/g, "$1 $2");
}

export const media = new MediaCache();
