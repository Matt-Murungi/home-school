import "server-only";
import { loadConfig } from "../config";
import { appwriteJwtProvider } from "../search/audit-sink";
import { settingsClientFor, type CoreSettingsClient } from "./core";

/** A client acting as the admin whose session secret is given (their JWT, never an API key). */
export function settingsClient(sessionSecret: string): CoreSettingsClient {
  const cfg = loadConfig();
  return settingsClientFor(cfg, appwriteJwtProvider(cfg, sessionSecret));
}
