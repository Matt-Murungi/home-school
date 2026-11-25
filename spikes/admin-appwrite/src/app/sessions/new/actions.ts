"use server";

import { redirect } from "next/navigation";
import { ID } from "node-appwrite";
import { DATABASE_ID, SESSIONS_TABLE_ID, STATUSES, SUBJECTS, tablesDB } from "@/lib/appwrite";

export type FormState = { errors: Record<string, string>; values: Record<string, string> };

export async function createSession(_prev: FormState, formData: FormData): Promise<FormState> {
  const values = Object.fromEntries(
    ["child_name", "teacher_name", "subject", "status", "scheduled_at", "amount_ugx"].map((k) => [
      k,
      String(formData.get(k) ?? "").trim(),
    ]),
  );
  const errors: Record<string, string> = {};

  if (!values.child_name) errors.child_name = "Child name is required";
  else if (values.child_name.length > 64) errors.child_name = "64 characters at most";
  if (!values.teacher_name) errors.teacher_name = "Teacher name is required";
  else if (values.teacher_name.length > 64) errors.teacher_name = "64 characters at most";
  if (!SUBJECTS.includes(values.subject as (typeof SUBJECTS)[number])) errors.subject = "Choose a subject";
  if (!STATUSES.includes(values.status as (typeof STATUSES)[number])) errors.status = "Choose a status";
  // <input type="datetime-local"> has no zone; the business runs on Kampala time (UTC+3, no DST).
  const scheduled = new Date(`${values.scheduled_at}:00+03:00`);
  if (!values.scheduled_at || Number.isNaN(scheduled.getTime())) errors.scheduled_at = "Enter a date and time";
  const amount = Number(values.amount_ugx);
  if (!Number.isInteger(amount) || amount < 0 || amount > 10_000_000)
    errors.amount_ugx = "Whole shillings between 0 and 10,000,000";

  if (Object.keys(errors).length > 0) return { errors, values };

  try {
    await tablesDB().createRow({
      databaseId: DATABASE_ID,
      tableId: SESSIONS_TABLE_ID,
      rowId: ID.unique(),
      data: {
        child_name: values.child_name,
        teacher_name: values.teacher_name,
        subject: values.subject,
        status: values.status,
        scheduled_at: scheduled.toISOString(),
        amount_ugx: amount,
      },
    });
  } catch (err) {
    return { errors: { form: `Appwrite rejected the row: ${(err as Error).message}` }, values };
  }
  redirect("/sessions");
}
