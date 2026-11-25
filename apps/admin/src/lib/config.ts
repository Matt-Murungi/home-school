export type EnvLabel = "Staging" | "Production" | "Local";

export type AdminConfig = {
  envLabel: EnvLabel;
  endpoint: string;
  projectId: string;
  databaseId: string;
};

/** Unknown or missing ADMIN_ENV maps to "Local", never to Production. */
export function loadConfig(env: Record<string, string | undefined> = process.env): AdminConfig {
  const raw = (env.ADMIN_ENV ?? "").trim().toLowerCase();
  const envLabel: EnvLabel =
    raw === "production" ? "Production" : raw === "staging" ? "Staging" : "Local";
  return {
    envLabel,
    endpoint: env.APPWRITE_ENDPOINT ?? "",
    projectId: env.APPWRITE_PROJECT_ID ?? "",
    databaseId: env.APPWRITE_DATABASE_ID || "main",
  };
}
