import "server-only";
import { loadConfig } from "../config";
import { MemoryRateLimiter, UnwiredAuditSink } from "./limits";
import { appwriteSearchSource } from "./appwrite";
import type { SearchDeps } from "./service";

// Per admin: 30 searches in 10 seconds (the box debounces to about 4 per second of typing).
const limiter = new MemoryRateLimiter({ max: 30, windowMs: 10_000 });
// TODO(M06 #41): replace with the real audit_log writer; until then searches are NOT persisted.
const audit = new UnwiredAuditSink();

/** Dependencies for one request: the Appwrite source acts as the admin whose session secret is given. */
export function searchDeps(sessionSecret: string): SearchDeps {
  return { source: appwriteSearchSource(loadConfig(), sessionSecret), limiter, audit };
}
