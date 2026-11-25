import "server-only";
import { Client, Query, TablesDB } from "node-appwrite";
import type { AdminConfig } from "../config";
import type { RawHit, SearchSource, SourceRequest } from "./types";

/**
 * Appwrite-backed search source. UNVERIFIED against a real project: it is written against the
 * schema on main (`appwrite/schema/*.json`, M01-M03) and M04 (`payment`, `safeguarding_case`, not
 * merged when this was written) and is only mock-tested. Needs the staging project to confirm
 * query operators, indexes, relevance and the SG-scope joins (memory.md "Blocked on owner").
 *
 * Every call is made with the signed-in admin's session (`setSession`), never an API key (ADR 0004),
 * so Appwrite row permissions are the first line of defence and the service re-checks on top.
 * No offset is ever sent: one page of `limit` rows per type (Appwrite totals cap at 5,000).
 */
export interface RowLister {
  listRows(args: {
    databaseId: string;
    tableId: string;
    queries: string[];
  }): Promise<{ rows: Record<string, unknown>[] }>;
}

type Row = Record<string, unknown>;
const str = (v: unknown) => (typeof v === "string" ? v : "");
const ids = (rows: Row[], key: string) => [
  ...new Set(rows.map((r) => str(r[key])).filter(Boolean)),
];
const live = () => Query.isNull("deleted_at");
const any = (conds: string[]) => (conds.length === 1 ? conds[0] : Query.or(conds));
const ID_LIKE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,35}$/;
/** Name lookups feeding session and SG-scope joins are bounded. */
const JOIN_LIMIT = 50;
const USER_FETCH_LIMIT = 100;

export class AppwriteSearchSource implements SearchSource {
  constructor(private o: { databaseId: string; rows: RowLister }) {}

  private async rows(tableId: string, queries: string[]): Promise<Row[]> {
    return (await this.o.rows.listRows({ databaseId: this.o.databaseId, tableId, queries })).rows;
  }

  async search(req: SourceRequest): Promise<RawHit[]> {
    switch (req.type) {
      case "parent":
        return this.people(req, "parent_profile");
      case "teacher":
        return this.people(req, "teacher_profile");
      case "child":
        return this.children(req);
      case "session":
        return this.sessions(req);
      case "payment":
        return this.payments(req);
      case "topic":
        return this.catalogue(req, "topic", Query.contains("title", req.query.text), (r) => ({
          title: str(r.title),
          code: str(r.code),
        }));
      case "question":
        return this.catalogue(req, "question", Query.contains("stem", req.query.text), (r) => ({
          stem: str(r.stem),
          status: str(r.status),
        }));
      case "case":
        return this.cases(req);
    }
  }

  /** Safeguarding scope: the ids an open case points at. No open cases means nothing is searchable. */
  private async caseLinks() {
    const cases = await this.rows("safeguarding_case", [
      Query.notEqual("status", "closed"),
      live(),
      Query.select(["child_id", "teacher_id", "session_id"]),
      Query.limit(100),
    ]);
    return {
      childIds: ids(cases, "child_id"),
      teacherIds: ids(cases, "teacher_id"),
      sessionIds: ids(cases, "session_id"),
    };
  }

  /** parent/teacher: match on user_profile (name, phone, email), keep users that have the role's profile. */
  private async people(req: SourceRequest, profileTable: "parent_profile" | "teacher_profile") {
    const { text, phoneFragment } = req.query;
    const queries = [
      any([
        Query.contains("name", text),
        Query.contains("email", text),
        ...(phoneFragment ? [Query.contains("phone", phoneFragment)] : []),
      ]),
      live(),
      Query.limit(USER_FETCH_LIMIT),
    ];
    if (req.scope === "safeguarding") {
      const links = await this.caseLinks();
      let userIds: string[] = [];
      if (profileTable === "teacher_profile" && links.teacherIds.length) {
        userIds = ids(
          await this.rows("teacher_profile", [Query.equal("$id", links.teacherIds)]),
          "user_id",
        );
      } else if (profileTable === "parent_profile" && links.childIds.length) {
        const kids = await this.rows("child", [Query.equal("$id", links.childIds)]);
        const parents = ids(kids, "parent_id");
        if (parents.length)
          userIds = ids(
            await this.rows("parent_profile", [Query.equal("$id", parents)]),
            "user_id",
          );
      }
      if (userIds.length === 0) return [];
      queries.push(Query.equal("$id", userIds));
    }
    const users = await this.rows("user_profile", queries);
    if (users.length === 0) return [];
    const profiles = await this.rows(profileTable, [
      Query.equal(
        "user_id",
        users.map((u) => str(u.$id)),
      ),
      Query.limit(USER_FETCH_LIMIT),
    ]);
    const has = new Set(profiles.map((p) => str(p.user_id)));
    return users
      .filter((u) => has.has(str(u.$id)))
      .slice(0, req.limit)
      .map<RawHit>((u) => ({
        type: req.type,
        id: str(u.$id),
        linkedToCase: req.scope === "safeguarding" ? true : undefined,
        fields: {
          name: str(u.name),
          phone: str(u.phone),
          email: str(u.email),
          status: str(u.status),
        },
      }));
  }

  private async children(req: SourceRequest) {
    const queries = [Query.contains("name", req.query.text), live(), Query.limit(req.limit)];
    if (req.scope === "safeguarding") {
      const { childIds } = await this.caseLinks();
      if (childIds.length === 0) return [];
      queries.push(Query.equal("$id", childIds));
    }
    return (await this.rows("child", queries)).map<RawHit>((c) => ({
      type: "child",
      id: str(c.$id),
      linkedToCase: req.scope === "safeguarding" ? true : undefined,
      // Only what the result row shows. Date of birth, school and concerns are not fetched onward.
      fields: { name: str(c.name), class_level: str(c.class_level) },
    }));
  }

  private async sessions(req: SourceRequest) {
    const { text, kind, dateRange } = req.query;
    const conds: string[] = [];
    if (kind !== "date" && ID_LIKE.test(text)) conds.push(Query.equal("$id", text));
    if (dateRange) conds.push(Query.between("scheduled_start_at", dateRange.from, dateRange.to));
    if (kind !== "date") {
      const kids = await this.rows("child", [
        Query.contains("name", text),
        live(),
        Query.limit(JOIN_LIMIT),
      ]);
      if (kids.length) conds.push(Query.equal("child_id", ids(kids, "$id")));
      const users = await this.rows("user_profile", [
        Query.contains("name", text),
        live(),
        Query.limit(JOIN_LIMIT),
      ]);
      if (users.length) {
        const tps = await this.rows("teacher_profile", [Query.equal("user_id", ids(users, "$id"))]);
        if (tps.length) conds.push(Query.equal("teacher_id", ids(tps, "$id")));
      }
    }
    if (conds.length === 0) return [];
    const queries = [
      any(conds),
      live(),
      Query.orderDesc("scheduled_start_at"),
      Query.limit(req.limit),
    ];
    if (req.scope === "safeguarding") {
      const links = await this.caseLinks();
      const scoped = [
        ...(links.sessionIds.length ? [Query.equal("$id", links.sessionIds)] : []),
        ...(links.childIds.length ? [Query.equal("child_id", links.childIds)] : []),
      ];
      if (scoped.length === 0) return [];
      queries.push(any(scoped));
    }
    const sessions = await this.rows("session", queries);
    if (sessions.length === 0) return [];
    const childNames = new Map(
      (await this.rows("child", [Query.equal("$id", ids(sessions, "child_id"))])).map((c) => [
        str(c.$id),
        str(c.name),
      ]),
    );
    const tps = await this.rows("teacher_profile", [
      Query.equal("$id", ids(sessions, "teacher_id")),
    ]);
    const userOf = new Map(tps.map((t) => [str(t.$id), str(t.user_id)]));
    const userNames = new Map(
      (
        await this.rows("user_profile", [Query.equal("$id", [...userOf.values()].filter(Boolean))])
      ).map((u) => [str(u.$id), str(u.name)]),
    );
    return sessions.map<RawHit>((s) => ({
      type: "session",
      id: str(s.$id),
      linkedToCase: req.scope === "safeguarding" ? true : undefined,
      fields: {
        scheduled_start_at: str(s.scheduled_start_at),
        status: str(s.status),
        child_name: childNames.get(str(s.child_id)) ?? "",
        teacher_name: userNames.get(userOf.get(str(s.teacher_id)) ?? "") ?? "",
      },
    }));
  }

  private async payments(req: SourceRequest) {
    const rows = await this.rows("payment", [
      Query.contains("provider_reference", req.query.text),
      Query.orderDesc("requested_at"),
      Query.limit(req.limit),
    ]);
    return rows.map<RawHit>((p) => ({
      type: "payment",
      id: str(p.$id),
      // raw_callback, idempotency_key and failure_reason are never mapped.
      fields: {
        provider_reference: str(p.provider_reference),
        amount: typeof p.amount === "number" ? p.amount : undefined,
        status: str(p.status),
        requested_at: str(p.requested_at),
        confirmed_at: str(p.confirmed_at),
      },
    }));
  }

  private async catalogue(
    req: SourceRequest,
    table: "topic" | "question",
    match: string,
    map: (r: Row) => RawHit["fields"],
  ) {
    const queries = [match, Query.limit(req.limit)];
    if (table === "topic") queries.push(live());
    return (await this.rows(table, queries)).map<RawHit>((r) => ({
      type: req.type,
      id: str(r.$id),
      fields: map(r),
    }));
  }

  /** Cases have no human reference column: found by exact id or by category. */
  private async cases(req: SourceRequest) {
    const conds = [Query.contains("category", req.query.text)];
    if (ID_LIKE.test(req.query.text)) conds.push(Query.equal("$id", req.query.text));
    const rows = await this.rows("safeguarding_case", [any(conds), live(), Query.limit(req.limit)]);
    return rows.map<RawHit>((c) => ({
      type: "case",
      id: str(c.$id),
      fields: { category: str(c.category), status: str(c.status) }, // never the description
    }));
  }
}

/** Real source for one request: a client scoped to the admin's session secret. */
export function appwriteSearchSource(cfg: AdminConfig, sessionSecret: string): SearchSource {
  const client = new Client()
    .setEndpoint(cfg.endpoint)
    .setProject(cfg.projectId)
    .setSession(sessionSecret);
  const db = new TablesDB(client);
  return new AppwriteSearchSource({
    databaseId: cfg.databaseId,
    rows: {
      listRows: async ({ databaseId, tableId, queries }) => {
        const r = await db.listRows({ databaseId, tableId, queries, total: false });
        return { rows: r.rows as unknown as Row[] };
      },
    },
  });
}
