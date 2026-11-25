import { execSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const root = join(__dirname, "..");
function walk(dir: string, out: string[] = []): string[] {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

describe("no API key in the admin app", () => {
  const src = walk(join(root, "src")).filter((f) => /\.(ts|tsx)$/.test(f));
  it("never calls setKey or reads an API key variable", () => {
    for (const f of src) {
      const text = readFileSync(f, "utf8");
      expect(text, f).not.toMatch(/\.setKey\(/);
      expect(text, f).not.toMatch(/API_KEY|APPWRITE_KEY/);
    }
  });
  it("only server-only modules import node-appwrite", () => {
    for (const f of src) {
      const text = readFileSync(f, "utf8");
      if (/from "node-appwrite"/.test(text)) expect(text, f).toMatch(/import "server-only"/);
    }
  });
  it("client components do not import server modules", () => {
    for (const f of src) {
      const text = readFileSync(f, "utf8");
      if (/^["']use client["']/.test(text)) {
        expect(text, f).not.toMatch(/node-appwrite|lib\/auth\/appwrite|lib\/config"/);
      }
    }
  });
  it("the .env.example is empty of values and has no key variable", () => {
    const text = readFileSync(join(root, ".env.example"), "utf8");
    for (const line of text.split("\n")) {
      if (line.startsWith("#") || !line.trim()) continue;
      expect(line).toMatch(/^[A-Z_]+=$/);
      expect(line).not.toMatch(/KEY/);
    }
  });
  it.skipIf(!existsSync(join(root, ".next/static")))("the built client bundle has no API key reference", () => {
    const files = walk(join(root, ".next/static")).filter((f) => f.endsWith(".js"));
    expect(files.length).toBeGreaterThan(0);
    for (const f of files) {
      const text = readFileSync(f, "utf8");
      expect(text, f).not.toMatch(/APPWRITE_API_KEY|X-Appwrite-Key|node-appwrite/i);
    }
  });
  it("git tracks no .env file other than .env.example", () => {
    const out = execSync("git ls-files .", { cwd: root }).toString();
    const env = out.split("\n").filter((l) => /(^|\/)\.env/.test(l) && !l.endsWith(".env.example"));
    expect(env).toEqual([]);
  });
});
