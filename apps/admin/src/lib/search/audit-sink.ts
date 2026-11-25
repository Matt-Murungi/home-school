import "server-only";
import type { AdminConfig } from "../config";
import { MemoryAuditSink, type AuditEntry, type AuditSink } from "./limits";

/**
 * The real audit path of the admin search (SEC-6 / ADM-6, M06 #41): the entry goes to the core
 * service (`POST /v1/admin/audit/event`), which verifies the caller's Appwrite JWT and role and
 * writes the audit row with the actor, IP and request id it derives itself. The admin app has no
 * API key: it only forwards a short-lived JWT of the signed-in admin's own session.
 *
 * What is sent: event name, entity TYPES, query KIND and per-type COUNTS. Never the query text,
 * result rows, the actor or roles (the server knows who called).
 *
 * Any failure (no JWT, network, timeout, non-2xx) throws: the search service then answers 503 and
 * returns no results (fail closed).
 */
const EVENT_PATH = "/v1/admin/audit/event";
const JWT_CACHE_MS = 10 * 60_000; // Appwrite JWTs last 15 minutes

/** Staging or production is configured without a working audit path: search must not be served. */
export class SearchAuditUnwired extends Error {
  constructor(reason: string) {
    super(`search audit is not wired: ${reason}`);
    this.name = "SearchAuditUnwired";
  }
}

type Fetch = typeof fetch;

/**
 * Runs `fn` with an AbortSignal and rejects when `ms` pass, whatever `fn` is waiting for (connect,
 * headers, JWT, body read). The timer is cleared only when `fn` has completely finished, so a peer
 * that sends headers and then stalls cannot hang the search.
 */
async function withTimeout<T>(ms: number, fn: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const ctl = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const expired = new Promise<never>((_res, rej) => {
    timer = setTimeout(() => {
      ctl.abort();
      rej(new Error("timed out"));
    }, ms);
  });
  try {
    return await Promise.race([fn(ctl.signal), expired]);
  } finally {
    clearTimeout(timer);
  }
}

export class CoreAuditSink implements AuditSink {
  private base: string;
  private fetchFn: Fetch;
  private timeoutMs: number;
  private jwt: () => Promise<string>;
  constructor(o: {
    baseUrl: string;
    jwt: () => Promise<string>;
    fetch?: Fetch;
    timeoutMs?: number;
  }) {
    this.base = o.baseUrl.replace(/\/+$/, "");
    this.jwt = o.jwt;
    this.fetchFn = o.fetch ?? fetch;
    this.timeoutMs = o.timeoutMs ?? 3000;
  }
  async record(entry: AuditEntry): Promise<void> {
    await withTimeout(this.timeoutMs, async (signal) => {
      const token = await this.jwt();
      let res: Response;
      try {
        res = await this.fetchFn(`${this.base}${EVENT_PATH}`, {
          method: "POST",
          headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
          // explicit field list: nothing else can ride along
          body: JSON.stringify({
            event: entry.action,
            entity_types: entry.entityTypes,
            query_kind: entry.queryKind,
            results: entry.results,
          }),
          signal,
          cache: "no-store",
        });
      } catch {
        throw new Error("audit request failed");
      }
      if (!res.ok) throw new Error(`audit request rejected (${res.status})`);
      let ok = false;
      try {
        ok = ((await res.json()) as { recorded?: unknown }).recorded === true;
      } catch {
        /* not JSON */
      }
      if (!ok) throw new Error("audit request not confirmed");
    });
  }
}

/** A short-lived JWT of the admin's own Appwrite session (POST /account/jwts), cached ~10 minutes. */
export function appwriteJwtProvider(
  cfg: AdminConfig,
  sessionSecret: string,
  o: { fetch?: Fetch; now?: () => number; timeoutMs?: number } = {},
): () => Promise<string> {
  const fetchFn = o.fetch ?? fetch;
  const now = o.now ?? Date.now;
  let cached: { jwt: string; at: number } | null = null;
  return async () => {
    if (cached && now() - cached.at < JWT_CACHE_MS) return cached.jwt;
    const jwt = await withTimeout(o.timeoutMs ?? 3000, async (signal) => {
      let res: Response;
      try {
        res = await fetchFn(`${cfg.endpoint.replace(/\/+$/, "")}/account/jwts`, {
          method: "POST",
          headers: {
            "x-appwrite-project": cfg.projectId,
            "x-appwrite-session": sessionSecret,
            "content-type": "application/json",
          },
          signal,
          cache: "no-store",
        });
      } catch {
        throw new Error("could not obtain a JWT");
      }
      if (!res.ok) throw new Error(`could not obtain a JWT (${res.status})`);
      const j = ((await res.json().catch(() => ({}))) as { jwt?: unknown }).jwt;
      if (typeof j !== "string" || j === "") throw new Error("could not obtain a JWT");
      return j;
    });
    cached = { jwt, at: now() };
    return jwt;
  };
}

const isLocalhost = (u: URL) => u.hostname === "localhost" || u.hostname === "127.0.0.1";

/**
 * Staging and production REQUIRE the real sink: CORE_API_URL must be set and https. Local
 * development may run without it (a fake that persists nothing) or with a local core service.
 */
export function auditSinkFor(cfg: AdminConfig, jwt: () => Promise<string>): AuditSink {
  const deployed = cfg.deployed;
  if (!cfg.coreApiUrl) {
    if (deployed) throw new SearchAuditUnwired("CORE_API_URL is not set");
    return new MemoryAuditSink();
  }
  let url: URL;
  try {
    url = new URL(cfg.coreApiUrl);
  } catch {
    throw new SearchAuditUnwired("CORE_API_URL is not a URL");
  }
  if (url.protocol !== "https:" && !(!deployed && isLocalhost(url)))
    throw new SearchAuditUnwired("CORE_API_URL must be https");
  return new CoreAuditSink({ baseUrl: cfg.coreApiUrl, jwt });
}
