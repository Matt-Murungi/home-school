/** Pure text helpers shared by the server (masking, formatting) and the client (highlighting). */

export function maskPhone(v: string | undefined | null): string {
  if (v === undefined || v === null) return "";
  const s = v.trim();
  if (s.length < 7) return "•••";
  return `${s.slice(0, s.startsWith("+") ? 4 : 3)}•••••${s.slice(-3)}`;
}

export function maskEmail(v: string | undefined | null): string {
  if (v === undefined || v === null) return "";
  const m = /^([^@\s])[^@\s]*@([^@\s]+)$/.exec(v.trim());
  return m ? `${m[1]}•••@${m[2]}` : "•••";
}

export type Segment = { text: string; hit: boolean };

/**
 * Splits text into plain segments around case-insensitive matches. The result is rendered as text
 * nodes plus <mark>, never as HTML, so nothing in a name can inject markup. If lower-casing changes
 * the string length (e.g. "İ") the text is left unhighlighted rather than mis-sliced.
 */
export function highlightSegments(text: string, query: string): Segment[] {
  const whole = [{ text, hit: false }];
  if (!query) return whole;
  const lt = text.toLowerCase();
  const lq = query.toLowerCase();
  if (lt.length !== text.length || lq.length !== query.length) return whole;
  const out: Segment[] = [];
  let at = 0;
  for (let i = lt.indexOf(lq); i !== -1; i = lt.indexOf(lq, at)) {
    if (i > at) out.push({ text: text.slice(at, i), hit: false });
    out.push({ text: text.slice(i, i + lq.length), hit: true });
    at = i + lq.length;
  }
  if (at === 0) return whole;
  if (at < text.length) out.push({ text: text.slice(at), hit: false });
  return out;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "25 Nov 2026, 14:00" in Africa/Kampala (UTC+3, no DST). */
export function formatKampala(iso: unknown): string {
  if (typeof iso !== "string") return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const d = new Date(t + 3 * 3600_000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}, ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

/** Integer UGX with thousands separators. */
export function formatUgx(n: unknown): string {
  const v = typeof n === "number" ? n : Number(n);
  if (!Number.isSafeInteger(v)) return "";
  return `UGX ${String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}`;
}
