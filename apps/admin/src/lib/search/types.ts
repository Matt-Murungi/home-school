import type { EntityType, Scope } from "./policy";

export type QueryKind = "text" | "phone" | "date" | "reference";

export type QueryInfo = {
  /** Normalised text (NFKC, no control or bidi characters, single spaces, at most 64 characters). */
  text: string;
  kind: QueryKind;
  /** Digits to look for inside a stored phone number (no leading 0 / 00), or null. */
  phoneFragment: string | null;
  /** The Africa/Kampala day, as a UTC range, when the query is a date. */
  dateRange: { from: string; to: string } | null;
};

/** One page for one type: `limit` rows, never an offset (Appwrite list totals cap at 5,000). */
export type SourceRequest = { type: EntityType; query: QueryInfo; scope: Scope; limit: number };

export type RawHit = {
  type: EntityType;
  id: string;
  /** Set by the source for `scope: "safeguarding"` requests: the row belongs to an open case. */
  linkedToCase?: boolean;
  fields: Record<string, string | number | boolean | null | undefined>;
};

/** Runs as the signed-in admin (their session), never with an API key (ADR 0004). */
export interface SearchSource {
  search(req: SourceRequest): Promise<RawHit[]>;
}

export type Hit = {
  id: string;
  type: EntityType;
  title: string;
  subtitle: string;
  href: string | null;
};
export type Group = {
  type: EntityType;
  label: string;
  hits: Hit[];
  hasMore: boolean;
  seeAll: string | null;
};
