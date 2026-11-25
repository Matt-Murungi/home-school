export type EnvLabel = "Staging" | "Production" | "Local";

export type AdminConfig = {
  envLabel: EnvLabel;
  endpoint: string;
  projectId: string;
  databaseId: string;
  /** Base URL of the core service (audit events). Empty = not set. */
  coreApiUrl: string;
  /** Staging/production, or any production build (even with a Local or missing label). */
  deployed: boolean;
};

/** Unknown or missing ADMIN_ENV maps to "Local", never to Production. */
export function loadConfig(env: Record<string, string | undefined> = process.env): AdminConfig {
  const raw = (env.ADMIN_ENV ?? "").trim().toLowerCase();
  const productionBuild = env.NODE_ENV === "production";
  // A production build must name its environment: a missing or mistyped value (`prod`) would
  // otherwise read as Local and get development fallbacks (M06 round 3). `next build` collects
  // page data without the runtime environment, so that phase is exempt.
  if (
    productionBuild &&
    env.NEXT_PHASE !== "phase-production-build" &&
    !["local", "staging", "production"].includes(raw)
  )
    throw new Error("ADMIN_ENV must be one of: staging, production, local");
  const envLabel: EnvLabel =
    raw === "production" ? "Production" : raw === "staging" ? "Staging" : "Local";
  return {
    envLabel,
    endpoint: env.APPWRITE_ENDPOINT ?? "",
    projectId: env.APPWRITE_PROJECT_ID ?? "",
    databaseId: env.APPWRITE_DATABASE_ID || "main",
    coreApiUrl: (env.CORE_API_URL ?? "").trim(),
    deployed: envLabel !== "Local" || productionBuild,
  };
}
