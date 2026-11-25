import "server-only";
import type { AdminConfig } from "../config";
import { withTimeout } from "../search/audit-sink";
import type { ChangeRequest, HistoryPage, SettingsList, SettingView } from "./types";

/**
 * The admin app's only way to the settings API (H05 #134). Same pattern as `CoreAuditSink` (M06):
 * the signed-in admin's OWN short-lived Appwrite JWT in `Authorization`, no API key, a timeout on
 * every call (connect, headers and body read). The core service re-checks the role (super admin)
 * and writes the audit row with the actor it derives from that JWT; nothing here can name an actor.
 */
export class CoreSettingsError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public currentRevision?: number,
  ) {
    super(message);
    this.name = "CoreSettingsError";
  }
}

/** Staging or production without a working CORE_API_URL: settings cannot be shown or changed. */
export class SettingsUnwired extends Error {
  constructor(reason: string) {
    super(`settings are not wired: ${reason}`);
    this.name = "SettingsUnwired";
  }
}

type Fetch = typeof fetch;

export class CoreSettingsClient {
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
    this.timeoutMs = o.timeoutMs ?? 5000;
  }

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    return withTimeout(this.timeoutMs, async (signal) => {
      const token = await this.jwt();
      let res: Response;
      try {
        res = await this.fetchFn(`${this.base}${path}`, {
          method,
          headers: {
            authorization: `Bearer ${token}`,
            ...(body === undefined ? {} : { "content-type": "application/json" }),
          },
          body: body === undefined ? undefined : JSON.stringify(body),
          signal,
          cache: "no-store",
        });
      } catch {
        throw new CoreSettingsError(503, "unavailable", "settings service unreachable");
      }
      let json: unknown = null;
      try {
        json = await res.json();
      } catch {
        /* not JSON */
      }
      if (!res.ok) {
        const e = (
          json as {
            error?: { code?: unknown; message?: unknown; current_revision?: unknown };
          } | null
        )?.error;
        throw new CoreSettingsError(
          res.status,
          typeof e?.code === "string" ? e.code : "error",
          typeof e?.message === "string" ? e.message : `request failed (${res.status})`,
          typeof e?.current_revision === "number" ? e.current_revision : undefined,
        );
      }
      return json as T;
    });
  }

  list(): Promise<SettingsList> {
    return this.call("GET", "/v1/admin/settings");
  }

  history(key: string, before?: number): Promise<HistoryPage> {
    const q = before ? `?before=${encodeURIComponent(String(before))}` : "";
    return this.call("GET", `/v1/admin/settings/${encodeURIComponent(key)}/history${q}`);
  }

  change(req: ChangeRequest): Promise<SettingView> {
    const key = encodeURIComponent(req.key);
    if (req.op === "set")
      return this.call("PUT", `/v1/admin/settings/${key}`, {
        value: req.value,
        reason: req.reason,
        revision: req.revision,
        ...(req.effectiveAt ? { effective_at: req.effectiveAt } : {}),
      });
    const path = req.op === "rollback" ? "rollback" : "cancel-pending";
    return this.call("POST", `/v1/admin/settings/${key}/${path}`, {
      reason: req.reason,
      revision: req.revision,
    });
  }
}

const isLocalhost = (u: URL) => u.hostname === "localhost" || u.hostname === "127.0.0.1";

/** Staging and production REQUIRE CORE_API_URL over https; local development may use localhost. */
export function settingsClientFor(
  cfg: AdminConfig,
  jwt: () => Promise<string>,
): CoreSettingsClient {
  if (!cfg.coreApiUrl) throw new SettingsUnwired("CORE_API_URL is not set");
  let url: URL;
  try {
    url = new URL(cfg.coreApiUrl);
  } catch {
    throw new SettingsUnwired("CORE_API_URL is not a URL");
  }
  if (url.protocol !== "https:" && !(!cfg.deployed && isLocalhost(url)))
    throw new SettingsUnwired("CORE_API_URL must be https");
  return new CoreSettingsClient({ baseUrl: cfg.coreApiUrl, jwt });
}
