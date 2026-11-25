import { describe, expect, it } from "vitest";
import { loadConfig } from "@/lib/config";

describe("environment label from config", () => {
  it("maps staging and production", () => {
    expect(loadConfig({ ADMIN_ENV: "staging" }).envLabel).toBe("Staging");
    expect(loadConfig({ ADMIN_ENV: "production" }).envLabel).toBe("Production");
  });
  it("defaults to Local so a missing value is never mistaken for Production", () => {
    expect(loadConfig({}).envLabel).toBe("Local");
    expect(loadConfig({ ADMIN_ENV: "prod-ish" }).envLabel).toBe("Local");
  });
  it("reads endpoint and project id, no key", () => {
    const c = loadConfig({ APPWRITE_ENDPOINT: "https://x/v1", APPWRITE_PROJECT_ID: "p" });
    expect(c.endpoint).toBe("https://x/v1");
    expect(c.projectId).toBe("p");
    expect(Object.keys(c)).not.toContain("apiKey");
  });
});
