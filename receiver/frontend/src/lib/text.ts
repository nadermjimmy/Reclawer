/* Arabic-aware text matching, the same normalisation the server's search uses (receiver/search.py):
   NFKD + drop combining marks (tashkeel, hamza/madda on alef, Latin accents), drop tatweel, ى→ي, ة→ه, ٱ→ا,
   Arabic-Indic digits → 0-9, lower case. */

const EXTRA: Record<string, string> = { "ـ": "", "ى": "ي", "ة": "ه", "ٱ": "ا" };
for (let i = 0; i < 10; i++) {
  EXTRA[String.fromCharCode(0x0660 + i)] = String(i);
  EXTRA[String.fromCharCode(0x06f0 + i)] = String(i);
}

function normChar(ch: string) {
  return ch
    .normalize("NFKD")
    .toLowerCase()
    .replace(/\p{Mn}/gu, "")
    .replace(/[ـىةٱ٠-٩۰-۹]/g, (c) => EXTRA[c] ?? c);
}

export function normalize(s: string | null | undefined) {
  return s ? Array.from(s).map(normChar).join("") : "";
}

export const tokens = (q: string) => normalize(q).match(/[\p{L}\p{N}_]+/gu) ?? [];

/** True when every token of `q` appears in `s` (normalised). */
export function matchesAll(s: string, toks: string[]) {
  const n = normalize(s);
  return toks.every((t) => n.includes(t));
}

/** Highlight ranges [start, end) in the original string for any token (normalised substring match). */
export function rangesIn(s: string, toks: string[]): [number, number][] {
  if (!s || !toks.length) return [];
  const chars = Array.from(s);
  let norm = "";
  const idx: number[] = [];
  let offset = 0;
  chars.forEach((ch) => {
    const n = normChar(ch);
    for (let k = 0; k < n.length; k++) idx.push(offset);
    norm += n;
    offset += ch.length;
  });
  const found: [number, number][] = [];
  for (const t of new Set(toks)) {
    let i = norm.indexOf(t);
    while (i !== -1) {
      const lastStart = idx[i + t.length - 1];
      const lastLen = s.codePointAt(lastStart)! > 0xffff ? 2 : 1;
      found.push([idx[i], lastStart + lastLen]);
      i = norm.indexOf(t, i + t.length);
    }
  }
  found.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const r of found) {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push([...r]);
  }
  return merged;
}

/** Initials for an avatar: first letters of the first two words (works for Arabic too). */
export function initials(name: string) {
  const words = name.replace(/^\+/, "").split(/[\s\-_|·()[\],.]+/).filter((w) => /^[\p{L}\p{N}]/u.test(w));
  if (!words.length) return "?";
  if (/^\d/.test(words[0])) return words[0].slice(0, 2);
  return words.slice(0, 2).map((w) => Array.from(w)[0]).join("").toUpperCase();
}

/** "rtl" when the first strong letter is Arabic-script, else "ltr" (for a line that mixes a sender and a snippet). */
export function textDir(s: string | null | undefined): "rtl" | "ltr" {
  const m = (s ?? "").match(/\p{L}/u);
  return m && /[֐-ࣿיִ-﷿ﹰ-﻿]/.test(m[0]) ? "rtl" : "ltr";
}
