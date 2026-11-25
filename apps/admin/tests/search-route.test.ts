// @vitest-environment node
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FakeAuthProvider } from "@/lib/auth/fake";
import { FakeSearchSource } from "@/lib/search/fake";
import { MemoryAuditSink, MemoryRateLimiter } from "@/lib/search/limits";
import type { RawHit } from "@/lib/search/types";

let auth: FakeAuthProvider;
let cookieValue: string | undefined;
let deps: { source: FakeSearchSource; audit: MemoryAuditSink; limiter: MemoryRateLimiter };
let depsSecret: string | undefined;

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (n: string) =>
      n === "hs_admin_session" && cookieValue ? { value: cookieValue } : undefined,
  }),
  headers: async () => new Headers(),
}));
vi.mock("@/lib/auth/appwrite", () => ({
  AppwriteAuthProvider: class {
    constructor() {
      return auth;
    }
  },
}));
vi.mock("@/lib/search/server", () => ({
  searchDeps: (secret: string) => {
    depsSecret = secret;
    return deps;
  },
}));

import { POST } from "@/app/api/search/route";
import * as route from "@/app/api/search/route";

const records: RawHit[] = [
  {
    type: "parent",
    id: "p1",
    fields: { name: "Grace Namuli", phone: "+256772123456", email: "g@example.com" },
  },
  {
    type: "payment",
    id: "pay1",
    fields: { provider_reference: "MP240101.1234.A12345", amount: 45000, status: "confirmed" },
  },
  { type: "topic", id: "t1", fields: { title: "Fractions", code: "M1" } },
];
const users = (label: string) => ({
  email: `${label}@x.test`,
  password: "pw-pw-pw-pw",
  name: label,
  labels: [label],
  mfa: "totp" as const,
  code: "123456",
});
async function signIn(label: string) {
  const s = await auth.createPasswordSession(`${label}@x.test`, "pw-pw-pw-pw");
  await auth.completeMfa(s!, (await auth.startMfa(s!)).challengeId, "123456");
  cookieValue = s!;
}
const HOST = "http://admin.test";
function post(body: unknown, headers: Record<string, string> = {}, raw?: string) {
  return new NextRequest(`${HOST}/api/search`, {
    method: "POST",
    body: raw ?? JSON.stringify(body),
    headers: {
      host: "admin.test",
      "content-type": "application/json",
      ...(cookieValue ? { cookie: `hs_admin_session=${cookieValue}` } : {}),
      ...headers,
    },
  });
}
const redirectOf = async (p: Promise<Response>) => {
  try {
    await p;
  } catch (e) {
    return String((e as { digest?: string }).digest ?? e);
  }
  return null;
};

beforeEach(() => {
  auth = new FakeAuthProvider(
    ["opsadmin", "curator", "superadmin", "safeguardinglead", "parent"].map(users),
  );
  cookieValue = undefined;
  depsSecret = undefined;
  deps = {
    source: new FakeSearchSource(records),
    audit: new MemoryAuditSink(),
    limiter: new MemoryRateLimiter({ max: 100, windowMs: 1000 }, () => 0),
  };
});

describe("POST /api/search", () => {
  it("is guarded: the route exports POST only (no GET, so the query can never ride in a URL)", () => {
    expect(Object.keys(route).filter((k) => /^(GET|POST|PUT|PATCH|DELETE)$/.test(k))).toEqual([
      "POST",
    ]);
  });
  it("unauthenticated: redirected to the session-clear/login path, data source untouched", async () => {
    expect(await redirectOf(POST(post({ q: "grace" })))).toMatch(
      /NEXT_REDIRECT;.*\/api\/session\/clear/,
    );
    expect(deps.source.calls).toEqual([]);
  });
  it("a bogus cookie is also refused", async () => {
    cookieValue = "not-a-real-session-secret";
    expect(await redirectOf(POST(post({ q: "grace" })))).toMatch(/\/api\/session\/clear/);
  });
  it("a signed-in non-admin (parent label) is refused", async () => {
    await signIn("parent");
    expect(await redirectOf(POST(post({ q: "grace" })))).toMatch(/\/api\/session\/clear/);
  });
  it("superadmin alone is sent to /forbidden by requireAccess", async () => {
    await signIn("superadmin");
    expect(await redirectOf(POST(post({ q: "grace" })))).toMatch(/NEXT_REDIRECT;.*\/forbidden/);
    expect(deps.source.calls).toEqual([]);
  });
  it("opsadmin gets grouped JSON, not cacheable, no cookies set", async () => {
    await signIn("opsadmin");
    const res = await POST(post({ q: "MP240101" }));
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toMatch(/no-store/);
    expect(res.headers.get("content-type")).toMatch(/application\/json/);
    expect(res.headers.getSetCookie()).toEqual([]);
    const j = await res.json();
    expect(j.groups[0]).toMatchObject({ type: "payment" });
  });
  it("the data source is built from the caller's own session secret (acts as the admin, no API key)", async () => {
    await signIn("opsadmin");
    await POST(post({ q: "grace" }));
    expect(depsSecret).toBe(cookieValue);
  });
  it("curator can search but a phone number returns nothing", async () => {
    await signIn("curator");
    const j = await (await POST(post({ q: "0772123" }))).json();
    expect(j.groups).toEqual([]);
    const j2 = await (await POST(post({ q: "fraction" }))).json();
    expect(j2.groups[0].type).toBe("topic");
  });
  it("cross-site POST is refused after authentication", async () => {
    await signIn("opsadmin");
    const res = await POST(post({ q: "grace" }, { "sec-fetch-site": "cross-site" }));
    expect(res.status).toBe(403);
    expect(deps.source.calls).toEqual([]);
  });
  it("bad bodies: not JSON, wrong content type, empty, oversize -> 400/413, never 500", async () => {
    await signIn("opsadmin");
    expect((await POST(post(null, {}, "{not json"))).status).toBe(400);
    expect((await POST(post(null, { "content-type": "text/plain" }, '{"q":"grace"}'))).status).toBe(
      400,
    );
    expect((await POST(post(null, {}, ""))).status).toBe(400);
    expect((await POST(post({ q: "x".repeat(10_000) }))).status).toBe(413);
    expect((await POST(post({ q: 5 }))).status).toBe(400);
    expect((await POST(post({ q: "a" }))).status).toBe(400);
  });
  it("429 carries Retry-After", async () => {
    await signIn("opsadmin");
    deps.limiter = new MemoryRateLimiter({ max: 1, windowMs: 5000 }, () => 0);
    expect((await POST(post({ q: "grace" }))).status).toBe(200);
    const res = await POST(post({ q: "grace" }));
    expect(res.status).toBe(429);
    expect(Number(res.headers.get("retry-after"))).toBeGreaterThan(0);
  });
  it("hostile input is not echoed back anywhere in the response", async () => {
    await signIn("opsadmin");
    const q = "<script>alert(1)</script>";
    const res = await POST(post({ q }));
    expect(await res.text()).not.toContain("script");
  });
  it("a failing data source gives a generic 503", async () => {
    await signIn("opsadmin");
    deps.source = new FakeSearchSource(records, {
      failTypes: ["parent", "teacher", "child", "session", "payment"],
    });
    const res = await POST(post({ q: "grace" }));
    expect(res.status).toBe(503);
    expect(await res.text()).not.toMatch(/fake|boom|grace/i);
  });
});
