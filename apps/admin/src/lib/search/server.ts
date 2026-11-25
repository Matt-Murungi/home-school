import "server-only";
import { loadConfig } from "../config";
import { MemoryRateLimiter } from "./limits";
import { appwriteJwtProvider, auditSinkFor } from "./audit-sink";
import { appwriteSearchSource } from "./appwrite";
import type { SearchDeps } from "./service";

// Per admin: 30 searches in 10 seconds (the box debounces to about 4 per second of typing).
const limiter = new MemoryRateLimiter({ max: 30, windowMs: 10_000 });

/**
 * Dependencies for one request: the Appwrite source acts as the admin whose session secret is
 * given, and searches are audited through the core service with that admin's own JWT (M06 #41).
 * Throws `SearchAuditUnwired` in staging/production when the audit path is not configured: the
 * route answers 503 and no data is read.
 */
export function searchDeps(sessionSecret: string): SearchDeps {
  const cfg = loadConfig();
  const audit = auditSinkFor(cfg, appwriteJwtProvider(cfg, sessionSecret));
  return { source: appwriteSearchSource(cfg, sessionSecret), limiter, audit };
}
