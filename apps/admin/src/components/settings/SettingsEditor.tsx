"use client";

import { useState, type FormEvent } from "react";
import { ReasonField } from "@/components/ReasonField";
import type {
  HistoryEntry,
  HistoryPage,
  SettingsList,
  SettingValue,
  SettingView,
} from "@/lib/settings/types";
import {
  formatKampala,
  formatValue,
  kampalaLocalToIso,
  parseInput,
  sameValue,
} from "@/lib/settings/validate";

type Action = { kind: "edit" | "rollback" | "cancel"; key: string } | null;
type Notice = { tone: "ok" | "error"; text: string } | null;

const MIN_REASON = 10;

async function api(method: "GET" | "POST", url: string, body?: unknown) {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  let json: Record<string, unknown> = {};
  try {
    json = (await res.json()) as Record<string, unknown>;
  } catch {
    /* empty or not JSON */
  }
  return { ok: res.ok, status: res.status, json };
}

function describeError(status: number, json: Record<string, unknown>): string {
  if (json.error === "revision_conflict")
    return "This setting was changed by someone else since you opened it. Reload to see the new value, then review your change again.";
  if (json.error === "unavailable" || status >= 500)
    return "The settings service is not reachable right now. Nothing was changed.";
  if (status === 403) return "Only the super admin can change settings.";
  return typeof json.message === "string" ? json.message : "The change was refused.";
}

export function SettingsEditor({ initial }: { initial: SettingsList }) {
  const [list, setList] = useState<SettingsList>(initial);
  const [action, setAction] = useState<Action>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [history, setHistory] = useState<{ key: string; entries: HistoryEntry[] } | null>(null);

  async function reload() {
    const r = await api("GET", "/api/settings");
    if (r.ok) {
      setList(r.json as unknown as SettingsList);
      setAction(null);
      setNotice({ tone: "ok", text: "Settings reloaded." });
    } else setNotice({ tone: "error", text: describeError(r.status, r.json) });
  }

  async function showHistory(key: string) {
    if (history?.key === key) return setHistory(null);
    const r = await api("GET", `/api/settings?key=${encodeURIComponent(key)}`);
    if (r.ok) setHistory({ key, entries: (r.json as unknown as HistoryPage).changes });
    else setNotice({ tone: "error", text: describeError(r.status, r.json) });
  }

  function applied(s: SettingView, text: string) {
    setList((l) => ({ ...l, settings: l.settings.map((x) => (x.key === s.key ? s : x)) }));
    setAction(null);
    setHistory(null);
    setNotice({ tone: "ok", text });
  }

  return (
    <div className="settings">
      {notice && (
        <p role={notice.tone === "error" ? "alert" : "status"} className={`notice ${notice.tone}`}>
          {notice.text}{" "}
          {notice.tone === "error" && (
            <button type="button" onClick={reload}>
              Reload settings
            </button>
          )}
        </p>
      )}
      {list.groups.map((g) => {
        const rows = list.settings.filter((s) => s.group === g.id);
        if (rows.length === 0) return null;
        return (
          <section key={g.id} aria-labelledby={`group-${g.id}`} className="settings-group">
            <h2 id={`group-${g.id}`}>{g.title}</h2>
            {rows.map((s) => (
              <SettingCard
                key={s.key}
                s={s}
                action={action?.key === s.key ? action : null}
                history={history?.key === s.key ? history.entries : null}
                onAction={(kind) => {
                  setNotice(null);
                  setAction(kind ? { kind, key: s.key } : null);
                }}
                onHistory={() => showHistory(s.key)}
                onApplied={applied}
                onFailed={(text) => setNotice({ tone: "error", text })}
              />
            ))}
          </section>
        );
      })}
    </div>
  );
}

function SettingCard(props: {
  s: SettingView;
  action: Action;
  history: HistoryEntry[] | null;
  onAction: (kind: "edit" | "rollback" | "cancel" | null) => void;
  onHistory: () => void;
  onApplied: (s: SettingView, text: string) => void;
  onFailed: (text: string) => void;
}) {
  const { s, action } = props;
  return (
    <article className="setting" aria-label={s.key}>
      <h3>
        <code>{s.key}</code>
      </h3>
      <p>{s.description}</p>
      <dl className="setting-facts">
        <dt>Current</dt>
        <dd data-testid={`current-${s.key}`}>{formatValue(s.value, s.unit)}</dd>
        <dt>Default</dt>
        <dd>{formatValue(s.default, s.unit)}</dd>
        <dt>Source</dt>
        <dd>
          {s.source === "override" ? `Changed by an admin (revision ${s.revision})` : "Default"}
        </dd>
        <dt>Takes effect</dt>
        <dd>
          {s.applies === "next_day"
            ? "From the next Kampala midnight (or the date you pick)"
            : "Immediately (or the date you pick)"}
        </dd>
      </dl>
      {s.pending && (
        <p className="pending" role="note">
          Scheduled: <strong>{formatValue(s.pending.value, s.unit)}</strong> from{" "}
          {formatKampala(s.pending.effective_at)}
        </p>
      )}
      {!action && (
        <div className="setting-actions">
          {!s.pending && (
            <button type="button" onClick={() => props.onAction("edit")}>
              Edit {s.key}
            </button>
          )}
          {s.source === "override" && !s.pending && (
            <button type="button" onClick={() => props.onAction("rollback")}>
              Roll back {s.key}
            </button>
          )}
          {s.pending && (
            <button type="button" onClick={() => props.onAction("cancel")}>
              Cancel scheduled change to {s.key}
            </button>
          )}
          <button type="button" onClick={props.onHistory} aria-expanded={props.history !== null}>
            History of {s.key}
          </button>
        </div>
      )}
      {action?.kind === "edit" && <EditForm {...props} />}
      {(action?.kind === "rollback" || action?.kind === "cancel") && (
        <ReasonOnlyForm {...props} kind={action.kind} />
      )}
      {props.history && <HistoryTable entries={props.history} unit={s.unit} />}
    </article>
  );
}

function initialText(s: SettingView): string | Record<string, string> {
  if (typeof s.value === "number") return String(s.value);
  return Object.fromEntries(Object.entries(s.value).map(([k, v]) => [k, String(v)]));
}

function EditForm(props: Parameters<typeof SettingCard>[0]) {
  const { s } = props;
  const [text, setText] = useState<string | Record<string, string>>(initialText(s));
  const [when, setWhen] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<{ value?: string; reason?: string; when?: string }>({});

  const parsed = parseInput(s, text);
  const changed = parsed.ok && !sameValue(parsed.value, s.value);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const next: typeof errors = {};
    if (!parsed.ok) next.value = parsed.error;
    else if (!changed) next.value = "The new value is the same as the current value";
    if (reason.trim().length < MIN_REASON) next.reason = "Give a reason of at least 10 characters";
    let effectiveAt: string | undefined;
    if (when) {
      const iso = kampalaLocalToIso(when);
      if (!iso) next.when = "Enter a valid date and time";
      else if (new Date(iso).getTime() < Date.now())
        next.when = "A change cannot take effect in the past";
      else effectiveAt = iso;
    }
    setErrors(next);
    if (Object.keys(next).length || !parsed.ok) return;
    setBusy(true);
    const r = await api("POST", "/api/settings", {
      op: "set",
      key: s.key,
      value: parsed.value,
      reason: reason.trim(),
      revision: s.revision,
      ...(effectiveAt ? { effectiveAt } : {}),
    });
    setBusy(false);
    if (r.ok) {
      const view = r.json as unknown as SettingView;
      props.onApplied(
        view,
        view.pending
          ? `Saved: ${s.key} will change from ${formatKampala(view.pending.effective_at)}.`
          : `Saved: ${s.key} is now ${formatValue(view.value, s.unit)}.`,
      );
    } else props.onFailed(describeError(r.status, r.json));
  }

  const fields = s.kind === "map" ? (s.fields ?? []) : null;
  return (
    <form onSubmit={submit} noValidate aria-label={`Edit ${s.key}`}>
      {fields ? (
        fields.map((f) => (
          <div className="field" key={f.name}>
            <label htmlFor={`v-${f.name}`}>
              {f.name} ({f.min} to {f.max})
            </label>
            <input
              id={`v-${f.name}`}
              inputMode="numeric"
              value={(text as Record<string, string>)[f.name] ?? ""}
              onChange={(e) =>
                setText({ ...(text as Record<string, string>), [f.name]: e.target.value })
              }
            />
          </div>
        ))
      ) : (
        <div className="field">
          <label htmlFor="v-single">
            New value{s.unit ? ` (${s.unit})` : ""}
            {s.min !== undefined ? `, ${s.min} to ${s.max}` : ""}
          </label>
          <input
            id="v-single"
            inputMode="decimal"
            value={text as string}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
      )}
      {errors.value && (
        <p role="alert" className="field-error">
          {errors.value}
        </p>
      )}
      <p aria-live="polite" className="diff">
        Current: <strong>{formatValue(s.value, s.unit)}</strong> → New:{" "}
        <strong>{parsed.ok ? formatValue(parsed.value, s.unit) : "(not valid yet)"}</strong>
      </p>
      <div className="field">
        <label htmlFor="effective">Effective from (Kampala time, optional)</label>
        <input
          id="effective"
          type="datetime-local"
          value={when}
          onChange={(e) => setWhen(e.target.value)}
        />
        {errors.when && (
          <p role="alert" className="field-error">
            {errors.when}
          </p>
        )}
      </div>
      <ReasonField error={errors.reason} defaultValue={reason} onChange={setReason} />
      <button type="submit" disabled={busy}>
        {busy ? "Saving…" : "Save change"}
      </button>{" "}
      <button type="button" onClick={() => props.onAction(null)}>
        Cancel
      </button>
    </form>
  );
}

function ReasonOnlyForm(
  props: Parameters<typeof SettingCard>[0] & { kind: "rollback" | "cancel" },
) {
  const { s, kind } = props;
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | undefined>();

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (reason.trim().length < MIN_REASON)
      return setError("Give a reason of at least 10 characters");
    setError(undefined);
    setBusy(true);
    const r = await api("POST", "/api/settings", {
      op: kind,
      key: s.key,
      reason: reason.trim(),
      revision: s.revision,
    });
    setBusy(false);
    if (r.ok)
      props.onApplied(
        r.json as unknown as SettingView,
        kind === "rollback"
          ? `Rolled back ${s.key}.`
          : `Cancelled the scheduled change to ${s.key}.`,
      );
    else props.onFailed(describeError(r.status, r.json));
  }

  const label =
    kind === "rollback" ? "Roll back to the previous value" : "Cancel the scheduled change";
  return (
    <form onSubmit={submit} noValidate aria-label={`${label}: ${s.key}`}>
      <p>
        {kind === "rollback"
          ? "The previous value (or the default) is restored as a new revision; history is kept."
          : `The scheduled value ${s.pending ? formatValue(s.pending.value, s.unit) : ""} will not apply.`}
      </p>
      <ReasonField error={error} defaultValue={reason} onChange={setReason} />
      <button type="submit" disabled={busy}>
        {busy ? "Saving…" : label}
      </button>{" "}
      <button type="button" onClick={() => props.onAction(null)}>
        Cancel
      </button>
    </form>
  );
}

function HistoryTable({ entries, unit }: { entries: HistoryEntry[]; unit: string }) {
  return (
    <div className="table-scroll">
      <table aria-label="History">
        <thead>
          <tr>
            <th scope="col">Revision</th>
            <th scope="col">What</th>
            <th scope="col">From → to</th>
            <th scope="col">Effective</th>
            <th scope="col">Reason</th>
            <th scope="col">By</th>
          </tr>
        </thead>
        <tbody>
          {entries.length === 0 ? (
            <tr>
              <td colSpan={6}>No changes yet: the default applies.</td>
            </tr>
          ) : (
            entries.map((h) => (
              <tr key={h.revision}>
                <td>{h.revision}</td>
                <td>{h.op === "cancel" ? `cancelled revision ${h.cancels_revision}` : h.op}</td>
                <td>
                  {h.previous_value === null ? "" : formatValue(h.previous_value, unit)}
                  {h.value === null ? "" : ` → ${formatValue(h.value as SettingValue, unit)}`}
                </td>
                <td>{h.effective_at ? formatKampala(h.effective_at) : ""}</td>
                <td>{h.reason}</td>
                <td>{h.actor_id}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
