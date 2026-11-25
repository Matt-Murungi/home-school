import type { AdminRole } from "../roles";

/**
 * What each admin role may search (SR 6.2 "Access rules", F06 audit decisions). This is the SERVER
 * allowlist: the search route asks for exactly these types and the service re-checks every result.
 * A role that is not listed for a type never has that type queried, so a curator typing a phone
 * number cannot even reach the people tables.
 *
 *   opsadmin          children, home address, session logs, teacher documents and payments are Read
 *                     (SR 6.2): parents, teachers, children, sessions, payments.
 *   curator           catalogue and questions only (read/write): topics, questions.
 *   safeguardinglead  "Read (cases)" on children and addresses, Read on sessions and teacher
 *                     documents, no payments: only people and sessions linked to a safeguarding
 *                     case ("safeguarding" scope), plus the cases themselves.
 *   superadmin        no SR 6.2 column; Overview and Settings only (F06 audit F-3): nothing, unless
 *                     it also holds another label (SR 6.1 allows several roles).
 *
 * UNCONFIRMED guesses (Questions for the owner): ops searching the catalogue (SR says Read, but the
 * top-bar spec lists people, sessions, payments only, so it is left out); safeguarding lead
 * searching teachers; ops seeing safeguarding cases ("unless also lead").
 */
export const ENTITY_TYPES = [
  "parent",
  "teacher",
  "child",
  "session",
  "payment",
  "topic",
  "question",
  "case",
] as const;
export type EntityType = (typeof ENTITY_TYPES)[number];

/** "all": any row the type has. "safeguarding": only rows linked to a safeguarding case. */
export type Scope = "all" | "safeguarding";

export const ENTITY_LABEL: Record<EntityType, string> = {
  parent: "Parents",
  teacher: "Teachers",
  child: "Children",
  session: "Sessions",
  payment: "Payments",
  topic: "Topics",
  question: "Questions",
  case: "Safeguarding cases",
};

/** List screen for each type. Result rows and "see all" link here (no id, no query: no personal data in URLs). */
export const ENTITY_ROUTE: Record<EntityType, string> = {
  parent: "/families",
  teacher: "/teachers",
  child: "/families",
  session: "/sessions",
  payment: "/payments",
  topic: "/curriculum",
  question: "/question-bank",
  case: "/safeguarding",
};

/** Types whose rows are personal data (searching them is audit-logged, SEC-6). */
export const PERSONAL_TYPES: readonly EntityType[] = [
  "parent",
  "teacher",
  "child",
  "session",
  "payment",
  "case",
];

const ALLOW: Record<AdminRole, Partial<Record<EntityType, Scope>>> = {
  opsadmin: { parent: "all", teacher: "all", child: "all", session: "all", payment: "all" },
  curator: { topic: "all", question: "all" },
  safeguardinglead: {
    parent: "safeguarding",
    teacher: "safeguarding",
    child: "safeguarding",
    session: "safeguarding",
    case: "all",
  },
  superadmin: {},
};

/** Union over the held roles; the broader scope wins. Insertion order follows ENTITY_TYPES. */
export function searchableTypes(roles: readonly AdminRole[]): Map<EntityType, Scope> {
  const out = new Map<EntityType, Scope>();
  for (const t of ENTITY_TYPES) {
    let scope: Scope | null = null;
    for (const r of roles) {
      const s = ALLOW[r]?.[t];
      if (s === "all") scope = "all";
      else if (s && scope === null) scope = s;
    }
    if (scope) out.set(t, scope);
  }
  return out;
}

/** Phone and email are shown in full only to roles that act on them (ops contacts families). */
export function canRevealContact(roles: readonly AdminRole[]): boolean {
  return roles.includes("opsadmin");
}
