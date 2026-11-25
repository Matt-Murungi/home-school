import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, isSameOriginPost } from "@/lib/access";
import { runChange, runRead } from "@/lib/settings/service";
import { settingsClient } from "@/lib/settings/server";
import { requireAccess } from "@/lib/session";

const MAX_BODY_CHARS = 8192;
const NO_STORE = { "Cache-Control": "no-store" };
const reply = (status: number, body: unknown) =>
  NextResponse.json(body, { status, headers: NO_STORE });

/**
 * Settings (H05 #134, A-50). Super admin only (`requireAccess` + the core service re-check). Runs
 * as the signed-in admin: their session cookie yields their own JWT for the core service, which
 * writes the audit row. The browser never sends an actor, a revision history or an audit key, and
 * the body is parsed against a strict allow-list (src/lib/settings/service.ts).
 */
export async function GET(req: NextRequest) {
  const admin = await requireAccess("/api/settings");
  const secret = req.cookies.get(SESSION_COOKIE)?.value ?? "";
  try {
    const r = await runRead({
      admin,
      query: req.nextUrl.searchParams,
      client: settingsClient(secret),
    });
    return reply(r.status, r.body);
  } catch {
    return reply(503, { error: "unavailable" });
  }
}

export async function POST(req: NextRequest) {
  const admin = await requireAccess("/api/settings");
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
  try {
    const r = await runChange({ admin, body, client: settingsClient(secret) });
    return reply(r.status, r.body);
  } catch {
    return reply(503, { error: "unavailable" });
  }
}
