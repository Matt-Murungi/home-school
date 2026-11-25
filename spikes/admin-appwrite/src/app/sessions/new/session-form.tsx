"use client";

import { useActionState } from "react";
import { createSession, type FormState } from "./actions";

const initial: FormState = { errors: {}, values: {} };

export function SessionForm({ subjects, statuses }: { subjects: readonly string[]; statuses: readonly string[] }) {
  const [state, action, pending] = useActionState(createSession, initial);
  const err = (k: string) => state.errors[k] && <span className="error">{state.errors[k]}</span>;
  const v = state.values;

  return (
    <form action={action} className="stack" noValidate>
      {err("form")}
      <label>Child name<input name="child_name" defaultValue={v.child_name} maxLength={64} />{err("child_name")}</label>
      <label>Teacher name<input name="teacher_name" defaultValue={v.teacher_name} maxLength={64} />{err("teacher_name")}</label>
      <label>Subject
        <select name="subject" defaultValue={v.subject ?? ""}>
          <option value="" disabled>Choose…</option>
          {subjects.map((s) => <option key={s}>{s}</option>)}
        </select>{err("subject")}
      </label>
      <label>Status
        <select name="status" defaultValue={v.status ?? "scheduled"}>
          {statuses.map((s) => <option key={s}>{s}</option>)}
        </select>{err("status")}
      </label>
      <label>Scheduled (Kampala time)<input type="datetime-local" name="scheduled_at" defaultValue={v.scheduled_at} />{err("scheduled_at")}</label>
      <label>Amount (UGX)<input type="number" name="amount_ugx" min={0} step={1} defaultValue={v.amount_ugx} />{err("amount_ugx")}</label>
      <button type="submit" disabled={pending}>{pending ? "Saving…" : "Create session"}</button>
    </form>
  );
}
