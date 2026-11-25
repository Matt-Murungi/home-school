import type { AdminAccount } from "../auth/types";
import { canAccessPath } from "../roles";
import { normalizeQuery } from "./normalize";
import {
  ENTITY_LABEL,
  ENTITY_ROUTE,
  PERSONAL_TYPES,
  canRevealContact,
  searchableTypes,
  type EntityType,
} from "./policy";
import type { AuditSink, RateLimiter } from "./limits";
import { formatKampala, formatUgx, maskEmail, maskPhone } from "./text";
import type { Group, Hit, RawHit, SearchSource } from "./types";

export const MAX_PER_GROUP = 20;

export type SearchDeps = { source: SearchSource; limiter: RateLimiter; audit: AuditSink };
export type SearchResult =
  | { status: 200; body: { groups: Group[]; unavailable: EntityType[] } }
  | { status: 400; body: { error: "invalid" | "too_short" | "too_long" } }
  | { status: 403; body: { error: "forbidden" } }
  | { status: 429; body: { error: "rate_limited" }; retryAfterSeconds: number }
  | { status: 503; body: { error: "unavailable" } };

const clean = (v: unknown, max = 200): string =>
  (typeof v === "string" || typeof v === "number" ? String(v) : "")
    .replace(/[\p{Cc}\p{Cf}]/gu, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);
const firstName = (v: unknown) => clean(v).split(" ")[0] ?? "";
const join = (...parts: string[]) => parts.filter(Boolean).join(" · ");

/**
 * Builds a result row from a whitelist of fields per type. Nothing else the source returned (DOB,
 * school, addresses, callback payloads, answer keys, case descriptions) can reach the browser.
 * Children appear as first name and class only, as SR 6.2 does for teachers after an assignment ends.
 */
function toHit(raw: RawHit, reveal: boolean, href: string | null): Hit {
  const f = raw.fields;
  const contact = (): string =>
    join(
      reveal ? clean(f.phone) : maskPhone(clean(f.phone)),
      reveal ? clean(f.email) : maskEmail(clean(f.email)),
    );
  const base = { id: clean(raw.id, 64), type: raw.type, href };
  switch (raw.type) {
    case "parent":
    case "teacher":
      return {
        ...base,
        title: clean(f.name) || "Unnamed",
        subtitle: join(contact(), clean(f.status)),
      };
    case "child":
      return {
        ...base,
        title: join(firstName(f.name), clean(f.class_level, 20)) || "Child",
        subtitle: "Child",
      };
    case "session":
      return {
        ...base,
        title: `Session ${clean(raw.id, 64)}`,
        subtitle: join(
          formatKampala(f.scheduled_start_at),
          clean(f.teacher_name),
          firstName(f.child_name),
          clean(f.status),
        ),
      };
    case "payment":
      return {
        ...base,
        title: clean(f.provider_reference) || `Payment ${clean(raw.id, 64)}`,
        subtitle: join(
          formatUgx(f.amount),
          clean(f.status),
          formatKampala(f.confirmed_at ?? f.requested_at),
        ),
      };
    case "topic":
      return { ...base, title: clean(f.title) || "Topic", subtitle: clean(f.code) };
    case "question":
      return { ...base, title: clean(f.stem, 120) || "Question", subtitle: clean(f.status) };
    case "case":
      return {
        ...base,
        title: `Case ${clean(raw.id, 64)}`,
        subtitle: join(clean(f.category), clean(f.status)),
      };
  }
}

/**
 * Runs one search as `admin`. Order matters: the role allowlist first (a role with nothing to
 * search never reaches the source), then rate limit, then query validation. Every row the source
 * returns is checked again: right type, inside the role's scope, screen openable by the role.
 */
export async function runSearch(i: {
  admin: AdminAccount;
  body: unknown;
  deps: SearchDeps;
}): Promise<SearchResult> {
  const { admin, deps } = i;
  const allowed = searchableTypes(admin.roles);
  if (allowed.size === 0) return { status: 403, body: { error: "forbidden" } };

  const rate = deps.limiter.check(admin.id);
  if (!rate.allowed)
    return {
      status: 429,
      body: { error: "rate_limited" },
      retryAfterSeconds: rate.retryAfterSeconds,
    };

  const b = i.body;
  const q = b && typeof b === "object" && !Array.isArray(b) ? (b as { q?: unknown }).q : undefined;
  const n = normalizeQuery(q);
  if (!n.ok) return { status: 400, body: { error: n.reason } };

  const types = [...allowed.keys()];
  const settled = await Promise.allSettled(
    types.map((type) =>
      deps.source.search({
        type,
        query: n.query,
        scope: allowed.get(type)!,
        limit: MAX_PER_GROUP + 1,
      }),
    ),
  );
  if (settled.every((s) => s.status === "rejected"))
    return { status: 503, body: { error: "unavailable" } };

  const reveal = canRevealContact(admin.roles);
  const groups: Group[] = [];
  const unavailable: EntityType[] = [];
  const counts: Partial<Record<EntityType, number>> = {};
  settled.forEach((s, idx) => {
    const type = types[idx];
    if (s.status === "rejected") {
      unavailable.push(type);
      return;
    }
    const route = ENTITY_ROUTE[type];
    const openable = canAccessPath(route, admin.roles);
    const scope = allowed.get(type)!;
    const rows = (Array.isArray(s.value) ? s.value : []).filter(
      (r) =>
        r &&
        r.type === type &&
        typeof r.id === "string" &&
        r.id !== "" &&
        (scope === "all" || r.linkedToCase === true) &&
        openable,
    );
    counts[type] = Math.min(rows.length, MAX_PER_GROUP);
    if (rows.length === 0) return;
    groups.push({
      type,
      label: ENTITY_LABEL[type],
      hits: rows.slice(0, MAX_PER_GROUP).map((r) => toHit(r, reveal, route)),
      hasMore: rows.length > MAX_PER_GROUP,
      seeAll: rows.length > MAX_PER_GROUP ? route : null,
    });
  });

  const personal = types.filter((t) => PERSONAL_TYPES.includes(t));
  if (personal.length > 0) {
    try {
      await deps.audit.record({
        actorId: admin.id,
        action: "admin.search",
        roles: [...admin.roles],
        entityTypes: personal,
        queryKind: n.query.kind,
        results: Object.fromEntries(personal.map((t) => [t, counts[t] ?? 0])),
      });
    } catch {
      return { status: 503, body: { error: "unavailable" } };
    }
  }
  return { status: 200, body: { groups, unavailable } };
}
