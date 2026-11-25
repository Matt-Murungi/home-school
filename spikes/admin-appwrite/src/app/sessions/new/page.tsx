import Link from "next/link";
import { STATUSES, SUBJECTS } from "@/lib/appwrite";
import { SessionForm } from "./session-form";

export default function NewSessionPage() {
  return (
    <main className="narrow">
      <p><Link href="/sessions">‹ Sessions</Link></p>
      <h1>New session</h1>
      <SessionForm subjects={SUBJECTS} statuses={STATUSES} />
    </main>
  );
}
