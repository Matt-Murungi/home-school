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
  it("database id defaults to main and can be overridden", () => {
    expect(loadConfig({}).databaseId).toBe("main");
    expect(loadConfig({ APPWRITE_DATABASE_ID: "staging_db" }).databaseId).toBe("staging_db");
  });
});

describe("ADMIN_ENV is a strict enum in production builds (M06 round 3)", () => {
  const prod = { NODE_ENV: "production" };
  it.each(["prod", "prodution", "stage", "live", "", "   "])(
    "a production build refuses ADMIN_ENV=%j",
    (v) => {
      expect(() => loadConfig({ ...prod, ADMIN_ENV: v })).toThrow(/ADMIN_ENV/);
    },
  );
  it("a production build with ADMIN_ENV unset is refused", () => {
    expect(() => loadConfig(prod)).toThrow(/ADMIN_ENV/);
  });
  it.each(["staging", "production", "PRODUCTION", " Staging ", "local"])(
    "a production build accepts %j",
    (v) => {
      expect(() => loadConfig({ ...prod, ADMIN_ENV: v })).not.toThrow();
    },
  );
  it("the next build phase may run without it (page data collection)", () => {
    expect(() => loadConfig({ ...prod, NEXT_PHASE: "phase-production-build" })).not.toThrow();
  });
  it("outside production builds a typo is still Local (development convenience)", () => {
    expect(loadConfig({ ADMIN_ENV: "prod" }).envLabel).toBe("Local");
  });
  it("a Local label in a production build counts as deployed", () => {
    expect(loadConfig({ ...prod, ADMIN_ENV: "local" }).deployed).toBe(true);
    expect(loadConfig({ ADMIN_ENV: "local" }).deployed).toBe(false);
    expect(loadConfig({ ADMIN_ENV: "staging" }).deployed).toBe(true);
  });
});
