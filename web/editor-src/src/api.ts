// Thin JSON client for the engine API (same origin).

import type { Clip, Timeline } from "./model";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail || `HTTP ${status}`);
  }
  /** Endpoint not implemented on this engine. */
  get unavailable(): boolean {
    return this.status === 404 || this.status === 405 || this.status === 501;
  }
}

async function errorFrom(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  try {
    const j = await res.json();
    detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
  } catch {
    /* not JSON */
  }
  return new ApiError(res.status, detail);
}

export async function request<T = unknown>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = { method, signal, credentials: "same-origin", headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Network error — engine unreachable");
  }
  if (!res.ok) throw await errorFrom(res);
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export const get = <T>(p: string, signal?: AbortSignal) => request<T>("GET", p, undefined, signal);
export const post = <T>(p: string, body?: unknown, signal?: AbortSignal) => request<T>("POST", p, body ?? {}, signal);
export const put = <T>(p: string, body?: unknown) => request<T>("PUT", p, body);

export async function postBlob(path: string, body: unknown, signal?: AbortSignal): Promise<Blob> {
  const res = await fetch(path, { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" }, signal, credentials: "same-origin" });
  if (!res.ok) throw await errorFrom(res);
  return res.blob();
}

// ── types ────────────────────────────────────────────────────────────

export interface Project {
  id: string;
  name: string;
  kind: string;
  data: Timeline;
  version: number;
  locked_by: string | null;
  updated_at?: string | number;
  brand_kit_id?: string | null;
}

export interface Asset {
  id: string;
  kind: "image" | "video" | "audio" | "vector" | string;
  mime: string;
  url: string;
  prompt?: string | null;
  created_at?: string;
  params?: Record<string, unknown> & { filename?: string };
  filename?: string;
}

export interface MediaStatus {
  asset_id?: string;
  ready: boolean;
  probe?: { duration?: number; fps?: number; width?: number; height?: number; has_audio?: boolean; has_video?: boolean; still?: boolean };
  proxy?: string;
  thumbs?: string;
  thumbs_meta?: { tiles: number; tile_w: number; tile_h: number; every_s: number };
  peaks?: string;
  transcript?: string;
}

export type JobStatus = "pending" | "running" | "completed" | "failed" | "canceled";

export interface Job<R = Record<string, unknown>> {
  id: string;
  status: JobStatus;
  stage?: string;
  progress?: number;
  result?: R;
  error?: string;
}

export interface Me {
  id: string;
  email: string;
  name?: string;
  role: "viewer" | "reviewer" | "editor" | "admin" | string;
}

export interface Version {
  id?: number | string;
  version: number;
  label: string;
  author?: string;
  created_at?: string | number;
}

export interface TranscriptWord {
  word: string;
  start: number;
  end: number;
}

export interface Transcript {
  text: string;
  words: TranscriptWord[];
  duration?: number;
}

export interface FontFile {
  family: string;
  file: string;
  url: string;
  weight: number;
  variable: boolean;
}

export interface Brand {
  id: string;
  name: string;
  colors?: Record<string, string>;
  gradients?: Record<string, string[]>;
  fonts?: Record<string, { family: string; weight: number }>;
  styles?: Record<string, Record<string, unknown>>;
}

export interface TemplateSummary {
  id: string;
  name: string;
  category: string;
  description?: string;
  slots?: string[];
  aspects?: string[];
  duration_hint?: number;
}

export interface GraphicResult {
  tracks: Array<{ id: string; clips: Clip[] }>;
  duration: number;
}

export interface Comment {
  id: string;
  body: string;
  in_frame: number;
  out_frame?: number | null;
  version?: number | null;
  resolved?: boolean;
  author?: string;
  created_at?: string;
  drawing?: unknown;
}

// ── jobs ─────────────────────────────────────────────────────────────

export interface PollOptions {
  onProgress?: (job: Job) => void;
  signal?: AbortSignal;
  intervalMs?: number;
}

/** Poll a job until it finishes. Resolves with the completed job; rejects on failure. */
export async function waitJob<R = Record<string, unknown>>(id: string, opts: PollOptions = {}): Promise<Job<R>> {
  let delay = opts.intervalMs ?? 600;
  let errors = 0;
  for (;;) {
    if (opts.signal?.aborted) throw new DOMException("aborted", "AbortError");
    let job: Job<R>;
    try {
      job = await get<Job<R>>(`/api/jobs/${id}`, opts.signal);
      errors = 0;
    } catch (e) {
      if ((e as Error).name === "AbortError") throw e;
      if (++errors > 8) throw e;
      await sleep(1500);
      continue;
    }
    opts.onProgress?.(job as Job);
    if (job.status === "completed") return job;
    if (job.status === "failed") throw new ApiError(500, job.error || "job failed");
    if (job.status === "canceled") throw new ApiError(499, "canceled");
    await sleep(delay);
    delay = Math.min(2000, delay * 1.15);
  }
}

export const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export function downloadUrl(url: string, filename?: string): void {
  const a = document.createElement("a");
  a.href = url;
  if (filename) a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
