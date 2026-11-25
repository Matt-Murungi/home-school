import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import ts from "typescript";

/**
 * Static guard checker. It enumerates EVERY page, route handler, layout, default/template file under
 * `<root>/app` and every server action under `<root>`, parses each with the TypeScript compiler (so
 * comments and strings never count), and requires an actual top-level `await requireAccess("...")`
 * call in the code that serves the request. Files that are intentionally public must be listed in an
 * allowlist with a reason.
 */
export type Allowlist = Record<string, string>; // path relative to <root>/app (posix) -> reason

export type Violation = { file: string; kind: string; problem: string };

const ROUTING_FILES = /^(page|route|layout|default|template)\.(tsx?|jsx?)$/;
const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];

function walk(dir: string, out: string[] = []): string[] {
  if (!existsSync(dir)) return out;
  for (const f of readdirSync(dir)) {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

const posix = (p: string) => p.split(sep).join("/");

function parse(file: string) {
  const text = readFileSync(file, "utf8");
  return ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
}

type Fn = ts.FunctionDeclaration | ts.ArrowFunction | ts.FunctionExpression;

function fnBody(fn: Fn): ts.Block | null {
  return fn.body && ts.isBlock(fn.body) ? fn.body : null;
}

function resolveFn(sf: ts.SourceFile, node: ts.Expression | undefined): Fn | null {
  if (!node) return null;
  if (ts.isArrowFunction(node) || ts.isFunctionExpression(node)) return node;
  if (ts.isIdentifier(node)) {
    for (const s of sf.statements) {
      if (ts.isFunctionDeclaration(s) && s.name?.text === node.text) return s;
      if (ts.isVariableStatement(s))
        for (const d of s.declarationList.declarations)
          if (ts.isIdentifier(d.name) && d.name.text === node.text)
            return resolveFn(sf, d.initializer);
    }
  }
  return null;
}

function hasModifier(n: ts.Node, kind: ts.SyntaxKind) {
  return ts.canHaveModifiers(n) && !!ts.getModifiers(n)?.some((m) => m.kind === kind);
}

/** Exported functions by name (`export function X`, `export const X = ...`, `export default ...` as "default"). */
function exportedFns(sf: ts.SourceFile): Map<string, Fn | null> {
  const out = new Map<string, Fn | null>();
  for (const s of sf.statements) {
    if (ts.isFunctionDeclaration(s) && hasModifier(s, ts.SyntaxKind.ExportKeyword)) {
      out.set(
        hasModifier(s, ts.SyntaxKind.DefaultKeyword) ? "default" : (s.name?.text ?? "default"),
        s,
      );
    } else if (ts.isExportAssignment(s) && !s.isExportEquals) {
      out.set("default", resolveFn(sf, s.expression));
    } else if (ts.isVariableStatement(s) && hasModifier(s, ts.SyntaxKind.ExportKeyword)) {
      for (const d of s.declarationList.declarations)
        if (ts.isIdentifier(d.name)) out.set(d.name.text, resolveFn(sf, d.initializer));
    } else if (ts.isExportDeclaration(s) && s.exportClause && ts.isNamedExports(s.exportClause)) {
      for (const e of s.exportClause.elements)
        out.set(e.name.text, s.moduleSpecifier ? null : resolveFn(sf, e.propertyName ?? e.name));
    }
  }
  return out;
}

/** A guard call is `await <name>(...)` as a top-level statement (also `const x = await <name>(...)`). */
function guardCall(body: ts.Block, names: readonly string[]): ts.CallExpression | null {
  for (const s of body.statements) {
    let e: ts.Expression | undefined;
    if (ts.isExpressionStatement(s)) e = s.expression;
    else if (ts.isVariableStatement(s))
      e =
        s.declarationList.declarations.length === 1
          ? s.declarationList.declarations[0].initializer
          : undefined;
    if (e && ts.isAwaitExpression(e)) e = e.expression;
    else e = undefined;
    if (
      e &&
      ts.isCallExpression(e) &&
      ts.isIdentifier(e.expression) &&
      names.includes(e.expression.text)
    )
      return e;
  }
  return null;
}

/** URL route of an app-router file: route groups and parallel slots are not part of the URL. */
export function routeOf(relFile: string): string {
  const parts = relFile
    .split("/")
    .slice(0, -1)
    .filter((p) => !/^\(.*\)$/.test(p) && !p.startsWith("@"));
  return "/" + parts.join("/");
}

function checkFn(
  fn: Fn | null | undefined,
  names: readonly string[],
  route: string | null,
): string | null {
  const body = fn && fnBody(fn);
  if (!fn || !body) return "handler could not be resolved to a function body";
  const call = guardCall(body, names);
  if (!call) return `no top-level \`await ${names[0]}(...)\` call`;
  if (route !== null) {
    const arg = call.arguments[0];
    if (!arg || !(ts.isStringLiteral(arg) || ts.isNoSubstitutionTemplateLiteral(arg)))
      return "requireAccess needs a string literal route";
    const dyn = route.indexOf("/[");
    const ok =
      dyn === -1
        ? arg.text === route
        : arg.text === route.slice(0, dyn) || arg.text.startsWith(route.slice(0, dyn) + "/");
    if (!ok) return `requireAccess("${arg.text}") does not match the file's route ${route}`;
  }
  return null;
}

function usesServerDirective(sf: ts.SourceFile): boolean {
  const prologue = (stmts: readonly ts.Statement[]) => {
    for (const s of stmts) {
      if (ts.isExpressionStatement(s) && ts.isStringLiteral(s.expression)) {
        if (s.expression.text === "use server") return true;
      } else break;
    }
    return false;
  };
  return prologue(sf.statements);
}

export function scanApp(
  root: string,
  allow: Allowlist,
): { violations: Violation[]; checked: string[] } {
  const appDir = join(root, "app");
  const violations: Violation[] = [];
  const checked: string[] = [];
  const seenAllow = new Set<string>();
  const add = (file: string, kind: string, problem: string) =>
    violations.push({ file, kind, problem });

  for (const [k, reason] of Object.entries(allow))
    if (reason.trim().length < 20)
      add(k, "allowlist", "allowlist entry needs a reason (20+ characters)");

  // 1. Routing files under app/.
  for (const abs of walk(appDir)) {
    const base = abs.split(sep).pop()!;
    if (!ROUTING_FILES.test(base)) continue;
    const rel = posix(relative(appDir, abs));
    checked.push(rel);
    if (rel in allow) {
      seenAllow.add(rel);
      continue;
    }
    const sf = parse(abs);
    const kind = base.split(".")[0];
    const fns = exportedFns(sf);
    if (kind === "route") {
      const handlers = HTTP_METHODS.filter((m) => fns.has(m));
      if (handlers.length === 0) add(rel, kind, "route file exports no HTTP handler");
      for (const m of handlers) {
        const p = checkFn(fns.get(m), ["requireAccess"], routeOf(rel));
        if (p) add(rel, kind, `${m}: ${p}`);
      }
    } else if (kind === "layout" || kind === "template") {
      const p = checkFn(fns.get("default"), ["requireAccess", "currentAdmin"], null);
      if (p) add(rel, kind, p);
    } else {
      const p = checkFn(fns.get("default"), ["requireAccess"], routeOf(rel));
      if (p) add(rel, kind, p);
    }
  }

  // 2. Server actions anywhere under root: file-level "use server" and inline directives.
  for (const abs of walk(root)) {
    if (!/\.(tsx?|jsx?)$/.test(abs)) continue;
    const rel = posix(relative(root, abs));
    const sf = parse(abs);
    const relApp = posix(relative(appDir, abs));
    if (usesServerDirective(sf)) {
      checked.push(`action:${rel}`);
      for (const [name, fn] of exportedFns(sf)) {
        const p = checkFn(fn, ["requireAccess"], null);
        if (p) add(rel, "server-action", `${name}: ${p}`);
      }
    }
    const visit = (n: ts.Node) => {
      if (
        (ts.isFunctionDeclaration(n) || ts.isArrowFunction(n) || ts.isFunctionExpression(n)) &&
        fnBody(n)
      ) {
        const b = fnBody(n)!;
        const first = b.statements[0];
        if (
          first &&
          ts.isExpressionStatement(first) &&
          ts.isStringLiteral(first.expression) &&
          first.expression.text === "use server" &&
          !usesServerDirective(sf)
        ) {
          checked.push(`action:${rel}`);
          const p = checkFn(n, ["requireAccess"], null);
          if (p) add(rel, "server-action", `inline action: ${p}`);
        }
      }
      ts.forEachChild(n, visit);
    };
    if (relApp in allow) continue;
    visit(sf);
  }

  for (const k of Object.keys(allow))
    if (!seenAllow.has(k)) add(k, "allowlist", "allowlist entry matches no routing file (stale)");
  return { violations, checked };
}
