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
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/";
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
