import type { AdminRole } from "../roles";
import type { EntityType } from "./policy";
import type { QueryKind } from "./types";

export type RateDecision = { allowed: true } | { allowed: false; retryAfterSeconds: number };
export interface RateLimiter {
  check(key: string): RateDecision;
}

/**
 * Sliding-window limiter per admin, in process memory. It is a hook: with several instances each
 * one counts separately, so the real ceiling is max x instances. A shared limiter can replace it.
 */
export class MemoryRateLimiter implements RateLimiter {
  private hits = new Map<string, number[]>();
  constructor(
    private opts: { max: number; windowMs: number },
    private now: () => number = Date.now,
  ) {}
  check(key: string): RateDecision {
    const t = this.now();
    const recent = (this.hits.get(key) ?? []).filter((x) => x > t - this.opts.windowMs);
    if (recent.length >= this.opts.max) {
      this.hits.set(key, recent);
      return {
        allowed: false,
        retryAfterSeconds: Math.max(1, Math.ceil((recent[0] + this.opts.windowMs - t) / 1000)),
      };
    }
    recent.push(t);
    this.hits.set(key, recent);
    if (this.hits.size > 10_000)
      for (const [k, v] of this.hits)
        if (v[v.length - 1] <= t - this.opts.windowMs) this.hits.delete(k);
    return { allowed: true };
  }
}

/**
 * SEC-6 / ADM-6: a search that can return personal data is an admin action and is audit-logged.
 * The entry carries who, which roles, which types were searched and how many rows came back, and
 * deliberately NOT the query text or any result text (that would put personal data into the log).
 */
export type AuditEntry = {
  actorId: string;
  action: "admin.search";
  roles: AdminRole[];
  entityTypes: EntityType[];
  queryKind: QueryKind;
  results: Partial<Record<EntityType, number>>;
};
export interface AuditSink {
  record(entry: AuditEntry): Promise<void>;
}

export class MemoryAuditSink implements AuditSink {
  entries: AuditEntry[] = [];
  async record(entry: AuditEntry) {
    this.entries.push(entry);
  }
}

/** Placeholder until M06 (audit log service) provides the real sink. Nothing is persisted. */
export class UnwiredAuditSink implements AuditSink {
  async record() {}
}
