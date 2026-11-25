export const SESSION_COOKIE = "hs_admin_session";
const PUBLIC_PATHS = ["/login", "/api/session"];

export type AccessDecision = { action: "next" } | { action: "redirect"; to: string };

export function decideAccess(i: {
  pathname: string;
  search: string;
  hasSession: boolean;
}): AccessDecision {
  const isPublic = PUBLIC_PATHS.some((p) => i.pathname === p || i.pathname.startsWith(p + "/"));
  if (i.pathname === "/login" && i.hasSession) return { action: "redirect", to: "/" };
  if (isPublic || i.hasSession) return { action: "next" };
  return { action: "redirect", to: `/login?next=${encodeURIComponent(i.pathname + i.search)}` };
}

/**
 * Only same-site absolute paths are allowed as a post-login target. Control characters, spaces and
 * backslashes are refused (the URL parser strips tabs/newlines, so "/<TAB>/evil.com" would become
 * "//evil.com"), then the result is resolved and must stay on the same origin.
 */
export function safeNext(value: string | undefined | null): string {
  if (!value || value.length > 2048 || !value.startsWith("/") || value.startsWith("//")) return "/";
  if (/[\\\u0000-\u0020\u007f-\u009f]/.test(value)) return "/";
  try {
    const base = "http://same-site.invalid";
    const u = new URL(value, base);
    if (u.origin !== base) return "/";
  } catch {
    return "/";
  }
  return value;
}

/**
 * CSRF guard for state-changing form POSTs (login, MFA, logout): the request must come from this
 * site. Browsers always send Origin on POST and Sec-Fetch-Site on modern versions; a request
 * carrying neither is not a browser form post and is let through (it has no ambient cookie to abuse).
 */
export function isSameOriginPost(h: { get(name: string): string | null }): boolean {
  const site = h.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") return false;
  const origin = h.get("origin");
  if (origin) {
    const host = h.get("x-forwarded-host") ?? h.get("host");
    try {
      if (!host || new URL(origin).host !== host) return false;
    } catch {
      return false;
    }
  }
  return true;
}

/** Opaque token shape only (no whitespace, braces or control characters, sane length). Not a validity check. */
export function isPlausibleSessionCookie(v: string | undefined): boolean {
  return !!v && /^[A-Za-z0-9._~+/=-]{16,2048}$/.test(v);
}
