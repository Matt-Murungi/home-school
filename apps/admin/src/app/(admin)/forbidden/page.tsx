import Link from "next/link";

export default function ForbiddenPage() {
  return (
    <section>
      <h1>You do not have access to this page</h1>
      <p>Your role does not include this area. Ask a super admin if you need access.</p>
      <Link href="/">Back to overview</Link>
    </section>
  );
}
