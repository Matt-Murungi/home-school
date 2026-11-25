import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, isSameOriginPost } from "@/lib/access";
import { searchDeps } from "@/lib/search/server";
import { runSearch } from "@/lib/search/service";
import { requireAccess } from "@/lib/session";

const MAX_BODY_CHARS = 2048;
const NO_STORE = { "Cache-Control": "no-store" };
const reply = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  NextResponse.json(body, { status, headers: { ...NO_STORE, ...headers } });

/**
 * Top-bar search. POST with a JSON body so the query (a name, a phone number) never appears in a
 * URL, a referrer or an access log; there is deliberately no GET. It runs as the signed-in admin
 * (their session cookie is the only credential, no API key). Which entity types are searched is
 * decided on the server from the admin's roles (src/lib/search/policy.ts), not by the browser.
 */
export async function POST(req: NextRequest) {
  const admin = await requireAccess("/api/search");
  if (!isSameOriginPost(req.headers)) return reply(403, { error: "forbidden" });
  if (!(req.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json"))
    return reply(400, { error: "invalid" });
  let text: string;
  try {
    text = await req.text();
  } catch {
    return reply(400, { error: "invalid" });
  }
  if (text.length > MAX_BODY_CHARS) return reply(413, { error: "too_long" });
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return reply(400, { error: "invalid" });
  }
  const secret = req.cookies.get(SESSION_COOKIE)?.value ?? "";
  let result;
  try {
    result = await runSearch({ admin, body, deps: searchDeps(secret) });
  } catch {
    return reply(503, { error: "unavailable" });
  }
  return reply(
    result.status,
    result.body,
    result.status === 429 ? { "Retry-After": String(result.retryAfterSeconds) } : {},
  );
}
