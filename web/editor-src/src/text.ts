// Canvas port of engine/champstudio/render/text.py (wrap, shrink-to-fit,
// background pills, stroke, shadow, typewriter, karaoke highlight).

import type { CaptionWord, TextStyle } from "./model";
import { media } from "./media";

export interface Color {
  r: number;
  g: number;
  b: number;
  a: number;
}

export function parseColor(c: string | null | undefined, def: Color = { r: 255, g: 255, b: 255, a: 255 }): Color {
  if (!c) return def;
  let s = c.trim().replace(/^#/, "");
  if (s.length === 3 || s.length === 4) s = s.split("").map((ch) => ch + ch).join("");
  const r = parseInt(s.slice(0, 2), 16);
  const g = parseInt(s.slice(2, 4), 16);
  const b = parseInt(s.slice(4, 6), 16);
  const a = s.length >= 8 ? parseInt(s.slice(6, 8), 16) : 255;
  if ([r, g, b, a].some((v) => Number.isNaN(v))) return def;
  return { r, g, b, a };
}

export function css(c: string | null | undefined, def?: Color): string {
  const { r, g, b, a } = parseColor(c, def);
  return `rgba(${r},${g},${b},${(a / 255).toFixed(3)})`;
}

type Ctx = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;

let measureCtx: CanvasRenderingContext2D | null = null;
function mctx(): CanvasRenderingContext2D {
  if (!measureCtx) measureCtx = document.createElement("canvas").getContext("2d")!;
  return measureCtx;
}

function fontString(style: TextStyle, size: number): string {
  return `${Math.round(style.weight)} ${size}px ${media.cssFamily(style.font)}`;
}

function textWidth(ctx: Ctx, text: string, spacing: number): number {
  if (!text) return 0;
  return ctx.measureText(text).width + spacing * Math.max(0, [...text].length - 1);
}

export function wrap(ctx: Ctx, text: string, maxW: number, spacing = 0): string[] {
  const lines: string[] = [];
  for (const para of text.split("\n")) {
    let line = "";
    for (const word of para.split(" ")) {
      const trial = `${line} ${word}`.trim();
      if (textWidth(ctx, trial, spacing) <= maxW || !line) line = trial;
      else {
        lines.push(line);
        line = word;
      }
    }
    lines.push(line);
  }
  return lines;
}

interface Fit {
  lines: string[];
  size: number;
  spacing: number;
}

function fit(ctx: Ctx, text: string, style: TextStyle, w: number, h: number, scale: number): Fit {
  const maxSize = Math.max(6, Math.trunc(style.size * scale));
  const minSize = Math.max(6, Math.trunc((style.min_size ?? style.size * 0.35) * scale));
  const pad = style.bg_color ? style.bg_padding * maxSize : 0;
  let size = maxSize;
  for (;;) {
    ctx.font = fontString(style, size);
    const spacing = style.letter_spacing * size;
    const lines = wrap(ctx, text, Math.max(1, w - 2 * pad), spacing);
    const total = size * style.line_height * lines.length;
    const widest = Math.max(0, ...lines.map((l) => textWidth(ctx, l, spacing)));
    const fits = total <= h - 2 * pad && widest <= w - 2 * pad + 1;
    if (fits || style.fit === "none" || size <= minSize) return { lines, size, spacing };
    size = Math.max(minSize, Math.trunc(size * 0.92));
  }
}

function roundRect(ctx: Ctx, x1: number, y1: number, x2: number, y2: number, r: number): void {
  const w = x2 - x1;
  const hh = y2 - y1;
  const rr = Math.max(0, Math.min(r, w / 2, hh / 2));
  ctx.beginPath();
  ctx.roundRect(x1, y1, w, hh, rr);
  ctx.fill();
}

const cache = new Map<string, HTMLCanvasElement>();

/** Rasterize text into a w×h canvas (cached). */
export function renderText(text: string, style: TextStyle, w: number, h: number, scale: number, visibleChars: number | null = null, highlightWord: number | null = null): HTMLCanvasElement | null {
  w = Math.max(1, Math.round(w));
  h = Math.max(1, Math.round(h));
  if (!text.trim()) return null;
  const key = JSON.stringify([text, style, w, h, scale, visibleChars, highlightWord]);
  const hit = cache.get(key);
  if (hit) return hit;
  if (cache.size > 300) cache.clear();
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d")!;
  if (style.uppercase) text = text.toUpperCase();
  const { lines, size, spacing } = fit(ctx, text, style, w, h, scale);
  ctx.font = fontString(style, size);
  const m = ctx.measureText("Hg");
  const ascent = m.fontBoundingBoxAscent || size * 0.9;
  const descent = m.fontBoundingBoxDescent || size * 0.25;
  const lh = size * style.line_height;
  const blockH = lh * (lines.length - 1) + ascent + descent;
  const y0 = style.valign === "top" ? 0 : style.valign === "bottom" ? h - blockH : (h - blockH) / 2;
  const padX = style.bg_color ? style.bg_padding * size : 0;

  const placements: Array<{ x: number; y: number; word: string; idx: number }> = [];
  const boxes: Array<[number, number, number, number]> = [];
  let wi = 0;
  lines.forEach((line, i) => {
    const lw = textWidth(ctx, line, spacing);
    const x = style.align === "left" ? padX : style.align === "right" ? w - lw - padX : (w - lw) / 2;
    const y = y0 + i * lh;
    boxes.push([x, y, x + lw, y + ascent + descent]);
    let cursor = x;
    for (const word of line.split(" ")) {
      placements.push({ x: cursor, y, word, idx: wi++ });
      cursor += textWidth(ctx, `${word} `, spacing);
    }
  });

  if (style.bg_color) {
    const pad = style.bg_padding * size;
    ctx.fillStyle = css(style.bg_color);
    for (const [x1, y1, x2, y2] of boxes) {
      if (x2 - x1 < 1) continue;
      roundRect(ctx, x1 - pad, y1 - pad * 0.6, x2 + pad, y2 + pad * 0.6, style.bg_radius * size);
    }
  }

  const drawWords = (fill: string, off: number, hlFill: string | null, strokeW: number, strokeFill: string | null) => {
    let budget = visibleChars ?? 1e9;
    ctx.textBaseline = "alphabetic";
    for (const p of placements) {
      if (budget <= 0) break;
      const t = [...p.word].slice(0, Math.max(0, budget)).join("");
      budget -= [...p.word].length + 1;
      ctx.fillStyle = hlFill !== null && p.idx === highlightWord ? hlFill : fill;
      const chars = spacing ? [...t] : [t];
      let cx = p.x + off;
      for (const ch of chars) {
        if (strokeW > 0 && strokeFill) {
          ctx.lineWidth = strokeW * 2;
          ctx.lineJoin = "round";
          ctx.strokeStyle = strokeFill;
          ctx.strokeText(ch, cx, p.y + off + ascent);
        }
        ctx.fillText(ch, cx, p.y + off + ascent);
        cx += ctx.measureText(ch).width + spacing;
      }
    }
  };

  if (style.shadow) {
    const off = Math.max(1, Math.trunc(size * 0.06));
    ctx.save();
    ctx.filter = `blur(${Math.max(1, size * 0.08) / 2}px)`;
    drawWords(css(style.shadow_color, { r: 0, g: 0, b: 0, a: 170 }), off, null, 0, null);
    ctx.restore();
    ctx.font = fontString(style, size);
  }

  if (style.highlight_bg && highlightWord !== null) {
    ctx.fillStyle = css(style.highlight_bg);
    for (const p of placements) {
      if (p.idx !== highlightWord) continue;
      const ww = textWidth(ctx, p.word, spacing);
      const pp = size * 0.12;
      roundRect(ctx, p.x - pp, p.y - pp * 0.5, p.x + ww + pp, p.y + ascent + descent + pp * 0.5, size * 0.18);
    }
  }

  const strokeW = Math.round(style.stroke_width * scale);
  drawWords(css(style.color), 0, style.highlight_color ? css(style.highlight_color) : null, strokeW, style.stroke_color ? css(style.stroke_color, { r: 0, g: 0, b: 0, a: 255 }) : null);
  cache.set(key, canvas);
  return canvas;
}

/** Port of text.caption_lines: group word indices into display lines. */
export function captionLines(words: CaptionWord[], maxChars: number): number[][] {
  const groups: number[][] = [];
  let cur: number[] = [];
  let length = 0;
  words.forEach((w, i) => {
    const token = w.w;
    const gap = i ? w.s - words[i - 1]!.e : 0;
    if (cur.length && (length + 1 + token.length > maxChars || gap > 12)) {
      groups.push(cur);
      cur = [];
      length = 0;
    }
    cur.push(i);
    length += token.length + (length ? 1 : 0);
    if (/[.!?]$/.test(token) && length > maxChars * 0.5) {
      groups.push(cur);
      cur = [];
      length = 0;
    }
  });
  if (cur.length) groups.push(cur);
  return groups;
}

export function clearTextCache(): void {
  cache.clear();
}
