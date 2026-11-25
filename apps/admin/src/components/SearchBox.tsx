"use client";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { highlightSegments } from "@/lib/search/text";

type Hit = { id: string; type: string; title: string; subtitle: string; href: string | null };
type Group = { type: string; label: string; hits: Hit[]; hasMore: boolean; seeAll: string | null };
type Data = { groups: Group[]; unavailable: string[] };
type Status =
  | { kind: "idle" }
  | { kind: "short" }
  | { kind: "loading" }
  | { kind: "ok"; data: Data }
  | { kind: "error"; message: string; retry: boolean };
type Option = { id: string; label: string; sub: string; href: string | null; hl: boolean };

const DEBOUNCE_MS = 250;
const MIN_LETTERS = 2;
const MIN_WITH_DIGIT = 3;
const MAX_CHARS = 64;
const UNAVAILABLE_LABEL: Record<string, string> = {
  parent: "Parents",
  teacher: "Teachers",
  child: "Children",
  session: "Sessions",
  payment: "Payments",
  topic: "Topics",
  question: "Questions",
  case: "Safeguarding cases",
};

/** Only same-site absolute paths are ever navigated to (a hostile `javascript:` href is dropped). */
const safeHref = (h: string | null) => (h && /^\/(?!\/)[A-Za-z0-9/_-]*$/.test(h) ? h : null);
const longEnough = (q: string) => [...q].length >= (/\d/.test(q) ? MIN_WITH_DIGIT : MIN_LETTERS);

function Marked({ text, q }: { text: string; q: string }) {
  return (
    <>
      {highlightSegments(text, q).map((s, i) =>
        s.hit ? <mark key={i}>{s.text}</mark> : <span key={i}>{s.text}</span>,
      )}
    </>
  );
}

/**
 * Top-bar search: WAI-ARIA combobox (focus stays in the input, results are a listbox driven by
 * aria-activedescendant). `groups` is what the server will search for this admin; the server
 * decides again on every request. The query is sent in a POST body only, never put in a URL.
 */
export function SearchBox({ groups }: { groups: string[] }) {
  const router = useRouter();
  const uid = useId();
  const listId = `${uid}-list`;
  const input = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const [value, setValue] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [online, setOnline] = useState(true);
  const seq = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const q = value.trim().replace(/\s+/g, " ");
  const enabled = groups.length > 0;

  const run = useCallback(async (query: string) => {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    const mine = ++seq.current;
    setStatus({ kind: "loading" });
    setOpen(true);
    const fail = (message: string, retry = true) => {
      if (mine === seq.current) setStatus({ kind: "error", message, retry });
    };
    try {
      const res = await fetch("/api/search", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ q: query }),
        signal: ctl.signal,
        cache: "no-store",
      });
      if (mine !== seq.current) return; // a newer search owns the box
      if (res.redirected) return fail("Your session has ended. Sign in again.", false);
      if (res.status === 429) {
        const wait = Number(res.headers.get("retry-after")) || 10;
        return fail(`Too many searches. Try again in ${wait} seconds.`, false);
      }
      // An HTML answer means a login or error page stood in for the API.
      if (!(res.headers.get("content-type") ?? "").includes("application/json"))
        return fail("Your session has ended. Sign in again.", false);
      if (!res.ok) return fail("Couldn't search. Try again.");
      const j = (await res.json()) as Partial<Data>;
      if (mine !== seq.current) return;
      setStatus({
        kind: "ok",
        data: {
          groups: Array.isArray(j.groups) ? j.groups : [],
          unavailable: Array.isArray(j.unavailable) ? j.unavailable : [],
        },
      });
    } catch (e) {
      if ((e as { name?: string }).name === "AbortError") return;
      fail("Couldn't search. Try again.");
    }
  }, []);

  // Debounced search on every change of the (normalised) query.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (!enabled) return;
    if (q === "") {
      seq.current++;
      abort.current?.abort();
      setStatus({ kind: "idle" });
      setOpen(false);
      return;
    }
    if (!longEnough(q)) {
      seq.current++;
      abort.current?.abort();
      setStatus({ kind: "short" });
      setOpen(true);
      return;
    }
    if (!online) {
      seq.current++;
      abort.current?.abort();
      setStatus({
        kind: "error",
        message: "You are offline. Search needs a connection.",
        retry: false,
      });
      setOpen(true);
      return;
    }
    timer.current = setTimeout(() => void run(q), DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [q, online, enabled, run]);

  useEffect(() => {
    setOnline(typeof navigator === "undefined" ? true : navigator.onLine !== false);
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
      abort.current?.abort();
    };
  }, []);

  // Shortcuts: "/" (not while typing somewhere) and Ctrl/Cmd+K focus the box.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
      if ((e.key === "k" || e.key === "K") && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        input.current?.focus();
      } else if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const data = status.kind === "ok" ? status.data : null;
  const options: Option[] = [];
  const grouped = (data?.groups ?? []).map((g) => ({
    g,
    opts: [
      ...g.hits.map((h) => ({
        id: `${uid}-o${options.length + 0}-${h.id}`,
        label: h.title,
        sub: h.subtitle,
        href: h.href,
        hl: true,
      })),
      ...(g.seeAll
        ? [
            {
              id: "",
              label: `See all ${g.label.toLowerCase()}`,
              sub: "",
              href: g.seeAll,
              hl: false,
            },
          ]
        : []),
    ] as Option[],
  }));
  for (const x of grouped)
    for (const o of x.opts) {
      o.id = `${uid}-opt-${options.length}`;
      options.push(o);
    }
  const shown = open && status.kind !== "idle";
  const activeOpt = active >= 0 && active < options.length ? options[active] : null;

  const go = (o: Option | null) => {
    const href = o && safeHref(o.href);
    if (!href) return;
    setOpen(false);
    router.push(href);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const n = options.length;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (n === 0) return;
      e.preventDefault();
      setOpen(true);
      setActive((a) => (e.key === "ArrowDown" ? (a + 1) % n : a <= 0 ? n - 1 : a - 1));
    } else if (e.key === "Home" && shown && n > 0) {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End" && shown && n > 0) {
      e.preventDefault();
      setActive(n - 1);
    } else if (e.key === "Enter") {
      if (activeOpt) {
        e.preventDefault();
        go(activeOpt);
      }
    } else if (e.key === "Escape") {
      if (shown) setOpen(false);
      else setValue("");
      setActive(-1);
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  const total = grouped.reduce((n, x) => n + x.g.hits.length, 0);
  const message =
    status.kind === "loading"
      ? "Searching…"
      : status.kind === "ok"
        ? total === 0
          ? ""
          : `${total} result${total === 1 ? "" : "s"}`
        : "";

  return (
    <div className="search" ref={root}>
      <input
        ref={input}
        type="text"
        role="combobox"
        aria-label="Search"
        aria-autocomplete="list"
        aria-expanded={shown && (options.length > 0 || status.kind === "ok")}
        aria-controls={listId}
        aria-activedescendant={shown && activeOpt ? activeOpt.id : undefined}
        aria-keyshortcuts="/ Control+K Meta+K"
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        maxLength={MAX_CHARS}
        disabled={!enabled}
        placeholder={enabled ? `Search ${groups.join(", ")}` : "Search"}
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          setActive(-1);
        }}
        onFocus={() => status.kind !== "idle" && setOpen(true)}
        onKeyDown={onKeyDown}
      />
      {!enabled && <small className="search-note">Search is not available for your role.</small>}
      <div role="status" aria-live="polite" className="sr-only">
        {message}
      </div>
      {shown && (
        <div className="search-panel">
          {status.kind === "short" && (
            <p className="search-note">
              Type at least {/\d/.test(q) ? MIN_WITH_DIGIT : MIN_LETTERS} characters.
            </p>
          )}
          {status.kind === "loading" && (
            <p className="search-note" aria-hidden="true">
              Searching…
            </p>
          )}
          {status.kind === "error" && (
            <p role="alert" className="search-note">
              {status.message}{" "}
              {status.retry && (
                <button type="button" onClick={() => void run(q)}>
                  Retry
                </button>
              )}
            </p>
          )}
          {data && data.unavailable.length > 0 && (
            <p className="search-note">
              {data.unavailable.map((t) => UNAVAILABLE_LABEL[t] ?? t).join(", ")} could not be
              searched.
            </p>
          )}
          {data && total === 0 && (
            <p role="status" className="search-note">
              No results
            </p>
          )}
          <div
            id={listId}
            role="listbox"
            aria-label="Search results"
            hidden={!data || options.length === 0}
          >
            {grouped.map(({ g, opts }) => (
              <div key={g.type} role="group" aria-label={g.label}>
                <div className="search-group" aria-hidden="true">
                  {g.label}
                </div>
                {opts.map((o) => (
                  <div
                    key={o.id}
                    id={o.id}
                    role="option"
                    aria-selected={activeOpt?.id === o.id}
                    className="search-option"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => go(o)}
                  >
                    <span className="search-title">
                      {o.hl ? <Marked text={o.label} q={q} /> : o.label}
                    </span>
                    {o.sub && (
                      <small className="search-sub">
                        {o.hl ? <Marked text={o.sub} q={q} /> : o.sub}
                      </small>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
