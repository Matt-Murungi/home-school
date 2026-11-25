// @vitest-environment node
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadConfig } from "@/lib/config";
import {
  CoreAuditSink,
  SearchAuditUnwired,
  appwriteJwtProvider,
  auditSinkFor,
} from "@/lib/search/audit-sink";
import { MemoryAuditSink, type AuditEntry } from "@/lib/search/limits";

const entry: AuditEntry = {
  actorId: "u_ops",
  action: "admin.search",
  roles: ["opsadmin"],
  entityTypes: ["parent", "payment"],
  queryKind: "phone",
  results: { parent: 3, payment: 0 },
};
const CORE = "https://core.example.test";
const ok = () => new Response(JSON.stringify({ recorded: true }), { status: 200 });

describe("CoreAuditSink", () => {
  it("POSTs the event to the core service with the admin's JWT and nothing else", async () => {
    const f = vi.fn(async () => ok());
    await new CoreAuditSink({ baseUrl: CORE, jwt: async () => "jwt-1", fetch: f }).record(entry);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${CORE}/v1/admin/audit/event`);
    expect(init.method).toBe("POST");
    const h = new Headers(init.headers);
    expect(h.get("authorization")).toBe("Bearer jwt-1");
    expect(h.get("content-type")).toBe("application/json");
    expect(JSON.parse(String(init.body))).toEqual({
      event: "admin.search",
      entity_types: ["parent", "payment"],
      query_kind: "phone",
      results: { parent: 3, payment: 0 },
    });
  });
  it("never sends the actor, roles, query text or anything beyond ids/counts/types", async () => {
    const f = vi.fn(async () => ok());
    await new CoreAuditSink({ baseUrl: CORE, jwt: async () => "j", fetch: f }).record({
      ...entry,
      // @ts-expect-error a stray field must not be forwarded
      query: "CANARY-GRACE",
    });
    const body = String((f.mock.calls[0] as unknown as [string, RequestInit])[1].body);
    expect(body).not.toMatch(/CANARY|u_ops|opsadmin|actor|roles/);
  });
  it("tolerates a trailing slash in the base URL", async () => {
    const f = vi.fn(async () => ok());
    await new CoreAuditSink({ baseUrl: `${CORE}/`, jwt: async () => "j", fetch: f }).record(entry);
    expect((f.mock.calls[0] as unknown as [string])[0]).toBe(`${CORE}/v1/admin/audit/event`);
  });
  it.each([401, 403, 422, 500, 503])("a %i answer is an error (fail closed)", async (status) => {
    const sink = new CoreAuditSink({
      baseUrl: CORE,
      jwt: async () => "j",
      // even a body that claims success is not trusted on an error status
      fetch: async () => new Response(JSON.stringify({ recorded: true }), { status }),
    });
    await expect(sink.record(entry)).rejects.toThrow();
  });
  it("a network error, a JWT failure and a timeout are errors too", async () => {
    await expect(
      new CoreAuditSink({
        baseUrl: CORE,
        jwt: async () => "j",
        fetch: async () => {
          throw new TypeError("connection refused");
        },
      }).record(entry),
    ).rejects.toThrow();
    await expect(
      new CoreAuditSink({
        baseUrl: CORE,
        jwt: async () => {
          throw new Error("session expired");
        },
        fetch: async () => ok(),
      }).record(entry),
    ).rejects.toThrow();
    await expect(
      new CoreAuditSink({
        baseUrl: CORE,
        jwt: async () => "j",
        timeoutMs: 20,
        fetch: (_u, init) =>
          new Promise((_res, rej) =>
            init?.signal?.addEventListener("abort", () => rej(new Error("aborted"))),
          ),
      }).record(entry),
    ).rejects.toThrow();
  });
  it("a 200 without recorded:true is an error", async () => {
    const sink = new CoreAuditSink({
      baseUrl: CORE,
      jwt: async () => "j",
      fetch: async () => new Response("{}", { status: 200 }),
    });
    await expect(sink.record(entry)).rejects.toThrow();
  });
  it("error messages never contain the JWT", async () => {
    const sink = new CoreAuditSink({
      baseUrl: CORE,
      jwt: async () => "SECRET-JWT",
      fetch: async () => new Response("SECRET-JWT", { status: 500 }),
    });
    await expect(sink.record(entry)).rejects.not.toThrow(/SECRET-JWT/);
  });
});

describe("appwriteJwtProvider (account JWT from the admin's own session, no API key)", () => {
  const cfg = loadConfig({ APPWRITE_ENDPOINT: "https://aw.test/v1", APPWRITE_PROJECT_ID: "proj" });
  it("calls POST /account/jwts with the session header and returns the jwt", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ jwt: "J1" }), { status: 201 }));
    const get = appwriteJwtProvider(cfg, "sess-secret", { fetch: f, now: () => 0 });
    expect(await get()).toBe("J1");
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://aw.test/v1/account/jwts");
    expect(init.method).toBe("POST");
    const h = new Headers(init.headers);
    expect(h.get("x-appwrite-project")).toBe("proj");
    expect(h.get("x-appwrite-session")).toBe("sess-secret");
    expect(h.get("x-appwrite-key")).toBeNull();
  });
  it("caches a JWT for a short time, then asks again", async () => {
    let t = 0;
    let n = 0;
    const f = vi.fn(async () => new Response(JSON.stringify({ jwt: `J${++n}` }), { status: 201 }));
    const get = appwriteJwtProvider(cfg, "s", { fetch: f, now: () => t });
    expect(await get()).toBe("J1");
    t = 60_000;
    expect(await get()).toBe("J1");
    t = 11 * 60_000;
    expect(await get()).toBe("J2");
  });
  it("an Appwrite error is an error, not an empty token", async () => {
    const get = appwriteJwtProvider(cfg, "s", {
      fetch: async () => new Response("{}", { status: 401 }),
      now: () => 0,
    });
    await expect(get()).rejects.toThrow();
    const get2 = appwriteJwtProvider(cfg, "s", {
      fetch: async () => new Response("{}", { status: 201 }),
      now: () => 0,
    });
    await expect(get2()).rejects.toThrow();
  });
});

describe("auditSinkFor: deployed environments refuse an unwired sink", () => {
  const jwt = async () => "j";
  const base = { APPWRITE_ENDPOINT: "https://aw.test/v1", APPWRITE_PROJECT_ID: "p" };
  it.each(["staging", "production"])("%s without CORE_API_URL throws SearchAuditUnwired", (env) => {
    expect(() => auditSinkFor(loadConfig({ ...base, ADMIN_ENV: env }), jwt)).toThrow(
      SearchAuditUnwired,
    );
  });
  it.each(["staging", "production"])("%s with an http (non-TLS) core URL is refused", (env) => {
    expect(() =>
      auditSinkFor(loadConfig({ ...base, ADMIN_ENV: env, CORE_API_URL: "http://core.x" }), jwt),
    ).toThrow(SearchAuditUnwired);
  });
  it.each(["staging", "production"])("%s with an https core URL gets the real sink", (env) => {
    const s = auditSinkFor(loadConfig({ ...base, ADMIN_ENV: env, CORE_API_URL: CORE }), jwt);
    expect(s).toBeInstanceOf(CoreAuditSink);
  });
  it("unknown/unset ADMIN_ENV is Local (dev): a fake sink is allowed without a core URL", () => {
    expect(auditSinkFor(loadConfig(base), jwt)).toBeInstanceOf(MemoryAuditSink);
  });
  it("Local with a core URL uses the real sink (http://localhost allowed)", () => {
    const s = auditSinkFor(loadConfig({ ...base, CORE_API_URL: "http://localhost:8080" }), jwt);
    expect(s).toBeInstanceOf(CoreAuditSink);
  });
  it("config exposes CORE_API_URL without a key", () => {
    expect(loadConfig({ CORE_API_URL: CORE }).coreApiUrl).toBe(CORE);
    expect(loadConfig({}).coreApiUrl).toBe("");
  });
});

describe("nothing persists nothing any more", () => {
  const walk = (d: string, out: string[] = []) => {
    for (const f of readdirSync(d)) {
      const p = join(d, f);
      if (statSync(p).isDirectory()) walk(p, out);
      else out.push(p);
    }
    return out;
  };
  it("UnwiredAuditSink no longer exists in the app source", () => {
    for (const f of walk(join(__dirname, "..", "src")))
      expect(readFileSync(f, "utf8"), f).not.toMatch(/UnwiredAuditSink/);
  });
});

describe("timeouts cover the whole request including the body (fake timers)", () => {
  afterEach(() => vi.useRealTimers());
  const stalledBody = () => new Response(new ReadableStream({ start() {} }), { status: 200 });
  const cfg = loadConfig({ APPWRITE_ENDPOINT: "https://aw.test/v1", APPWRITE_PROJECT_ID: "p" });

  it("CoreAuditSink: headers then a stalled body still fails at the timeout", async () => {
    vi.useFakeTimers();
    const sink = new CoreAuditSink({
      baseUrl: CORE,
      jwt: async () => "j",
      timeoutMs: 3000,
      fetch: async () => stalledBody(),
    });
    const p = sink.record(entry);
    const assertion = expect(p).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(3001);
    await assertion;
  });
  it("CoreAuditSink: a request that never answers fails at the timeout", async () => {
    vi.useFakeTimers();
    const sink = new CoreAuditSink({
      baseUrl: CORE,
      jwt: async () => "j",
      timeoutMs: 3000,
      fetch: () => new Promise<Response>(() => {}),
    });
    const assertion = expect(sink.record(entry)).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(3001);
    await assertion;
  });
  it("CoreAuditSink: the JWT step is inside the same budget", async () => {
    vi.useFakeTimers();
    const sink = new CoreAuditSink({
      baseUrl: CORE,
      jwt: () => new Promise<string>(() => {}),
      timeoutMs: 3000,
      fetch: async () => ok(),
    });
    const assertion = expect(sink.record(entry)).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(3001);
    await assertion;
  });
  it("appwriteJwtProvider: no answer fails at the timeout", async () => {
    vi.useFakeTimers();
    const get = appwriteJwtProvider(cfg, "s", {
      fetch: () => new Promise<Response>(() => {}),
      timeoutMs: 3000,
    });
    const assertion = expect(get()).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(3001);
    await assertion;
  });
  it("appwriteJwtProvider: headers then a stalled body fails at the timeout", async () => {
    vi.useFakeTimers();
    const get = appwriteJwtProvider(cfg, "s", {
      fetch: async () => stalledBody(),
      timeoutMs: 3000,
    });
    const assertion = expect(get()).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(3001);
    await assertion;
  });
  it("a fast answer is not cut off", async () => {
    vi.useFakeTimers();
    await new CoreAuditSink({
      baseUrl: CORE,
      jwt: async () => "j",
      timeoutMs: 3000,
      fetch: async () => ok(),
    }).record(entry);
  });
});

describe("deployed means a production build too", () => {
  const jwt = async () => "j";
  const base = { APPWRITE_ENDPOINT: "https://aw.test/v1", APPWRITE_PROJECT_ID: "p" };
  it("NODE_ENV=production with a local label and no core URL is refused", () => {
    expect(() =>
      auditSinkFor(loadConfig({ ...base, NODE_ENV: "production", ADMIN_ENV: "local" }), jwt),
    ).toThrow(SearchAuditUnwired);
  });
  it("NODE_ENV=production does not allow http://localhost", () => {
    expect(() =>
      auditSinkFor(
        loadConfig({
          ...base,
          NODE_ENV: "production",
          ADMIN_ENV: "local",
          CORE_API_URL: "http://localhost:8080",
        }),
        jwt,
      ),
    ).toThrow(SearchAuditUnwired);
  });
});
