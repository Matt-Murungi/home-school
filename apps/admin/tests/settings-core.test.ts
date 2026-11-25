// @vitest-environment node
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { loadConfig } from "@/lib/config";
import {
  CoreSettingsClient,
  CoreSettingsError,
  SettingsUnwired,
  settingsClientFor,
} from "@/lib/settings/core";

const CORE = "https://core.example.test";
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe("CoreSettingsClient: the admin to core call pattern (as CoreAuditSink)", () => {
  it("sends the admin's own JWT and nothing like an API key", async () => {
    const f = vi.fn(async () => json({ groups: [], settings: [] }));
    await new CoreSettingsClient({ baseUrl: CORE, jwt: async () => "jwt-1", fetch: f }).list();
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${CORE}/v1/admin/settings`);
    const h = new Headers(init.headers);
    expect(h.get("authorization")).toBe("Bearer jwt-1");
    expect([...h.keys()].sort()).toEqual(["authorization"]);
    expect(init.cache).toBe("no-store");
  });
  it("a set is a PUT with exactly value, reason, revision (+ effective_at); the actor is never sent", async () => {
    const f = vi.fn(async () => json({ key: "k" }));
    const c = new CoreSettingsClient({ baseUrl: `${CORE}/`, jwt: async () => "j", fetch: f });
    await c.change({
      op: "set",
      key: "session.auto_confirm_after_hours",
      value: 24,
      reason: "because we said",
      revision: 3,
      effectiveAt: "2027-03-02T00:00:00.000Z",
    });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${CORE}/v1/admin/settings/session.auto_confirm_after_hours`);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({
      value: 24,
      reason: "because we said",
      revision: 3,
      effective_at: "2027-03-02T00:00:00.000Z",
    });
    expect(new Headers(init.headers).get("content-type")).toBe("application/json");
  });
  it("rollback and cancel are POSTs of reason and revision only; history is a GET with paging", async () => {
    const f = vi.fn(async () => json({}));
    const c = new CoreSettingsClient({ baseUrl: CORE, jwt: async () => "j", fetch: f });
    await c.change({ op: "rollback", key: "a.b", reason: "reason one", revision: 2 });
    await c.change({ op: "cancel", key: "a.b", reason: "reason two", revision: 3 });
    await c.history("a.b", 7);
    const calls = f.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map(([u, i]) => `${i.method} ${u.replace(CORE, "")}`)).toEqual([
      "POST /v1/admin/settings/a.b/rollback",
      "POST /v1/admin/settings/a.b/cancel-pending",
      "GET /v1/admin/settings/a.b/history?before=7",
    ]);
    expect(JSON.parse(String(calls[0][1].body))).toEqual({ reason: "reason one", revision: 2 });
  });
  it("a key is URL-encoded, never able to change the path", async () => {
    const f = vi.fn(async () => json({}));
    await new CoreSettingsClient({ baseUrl: CORE, jwt: async () => "j", fetch: f }).history(
      "../x?y#z",
    );
    expect((f.mock.calls[0] as unknown as [string])[0]).toBe(
      `${CORE}/v1/admin/settings/..%2Fx%3Fy%23z/history`,
    );
  });
  it("a core error becomes CoreSettingsError with code, message and current revision", async () => {
    const c = new CoreSettingsClient({
      baseUrl: CORE,
      jwt: async () => "j",
      fetch: async () =>
        json({ error: { code: "revision_conflict", message: "m", current_revision: 5 } }, 409),
    });
    const e = await c.list().catch((x) => x);
    expect(e).toBeInstanceOf(CoreSettingsError);
    expect(e).toMatchObject({ status: 409, code: "revision_conflict", currentRevision: 5 });
  });
  it("a non-JSON error body and a network error are errors, not crashes", async () => {
    const a = new CoreSettingsClient({
      baseUrl: CORE,
      jwt: async () => "j",
      fetch: async () => new Response("<html>", { status: 502 }),
    });
    expect(await a.list().catch((x) => x)).toMatchObject({ status: 502 });
    const b = new CoreSettingsClient({
      baseUrl: CORE,
      jwt: async () => "j",
      fetch: async () => {
        throw new TypeError("connection refused");
      },
    });
    expect(await b.list().catch((x) => x)).toMatchObject({ status: 503, code: "unavailable" });
  });
  it("a JWT failure is an error and nothing is sent", async () => {
    const f = vi.fn(async () => json({}));
    const c = new CoreSettingsClient({
      baseUrl: CORE,
      jwt: async () => {
        throw new Error("expired");
      },
      fetch: f,
    });
    await expect(c.list()).rejects.toThrow();
    expect(f).not.toHaveBeenCalled();
  });
  it("times out when the core stalls, whatever it is waiting for", async () => {
    const c = new CoreSettingsClient({
      baseUrl: CORE,
      jwt: async () => "j",
      timeoutMs: 20,
      fetch: (_u, init) =>
        new Promise((_res, rej) =>
          init?.signal?.addEventListener("abort", () => rej(new Error("aborted"))),
        ),
    });
    await expect(c.list()).rejects.toThrow();
    const slowJwt = new CoreSettingsClient({
      baseUrl: CORE,
      jwt: () => new Promise(() => {}),
      timeoutMs: 20,
      fetch: async () => json({}),
    });
    await expect(slowJwt.list()).rejects.toThrow(/timed out/);
  });
});

describe("settingsClientFor", () => {
  const cfg = (env: Record<string, string>) => loadConfig({ ADMIN_ENV: "staging", ...env });
  it("staging and production require CORE_API_URL over https", () => {
    expect(() => settingsClientFor(cfg({}), async () => "j")).toThrow(SettingsUnwired);
    expect(() =>
      settingsClientFor(cfg({ CORE_API_URL: "http://core.example.test" }), async () => "j"),
    ).toThrow(/https/);
    expect(() => settingsClientFor(cfg({ CORE_API_URL: "not a url" }), async () => "j")).toThrow(
      SettingsUnwired,
    );
    expect(() => settingsClientFor(cfg({ CORE_API_URL: CORE }), async () => "j")).not.toThrow();
  });
  it("local development may use a localhost core", () => {
    const local = loadConfig({ ADMIN_ENV: "local", CORE_API_URL: "http://localhost:8000" });
    expect(() => settingsClientFor(local, async () => "j")).not.toThrow();
    const prod = loadConfig({ ADMIN_ENV: "production", CORE_API_URL: "http://localhost:8000" });
    expect(() => settingsClientFor(prod, async () => "j")).toThrow(/https/);
  });
  it("the settings modules hold no API key and no Appwrite server SDK", () => {
    for (const f of ["core.ts", "server.ts", "service.ts", "validate.ts"]) {
      const text = readFileSync(join(__dirname, "../src/lib/settings", f), "utf8");
      expect(text).not.toMatch(/setKey|API_KEY|APPWRITE_KEY|node-appwrite/);
    }
  });
});
