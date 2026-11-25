import { cookies } from "next/headers";
import { SESSION_COOKIE } from "@/lib/access";
import { requireAccess } from "@/lib/session";
import { settingsClient } from "@/lib/settings/server";
import type { SettingsList } from "@/lib/settings/types";
import { SettingsEditor } from "@/components/settings/SettingsEditor";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  await requireAccess("/settings");
  const secret = (await cookies()).get(SESSION_COOKIE)?.value ?? "";
  let initial: SettingsList | null = null;
  try {
    initial = await settingsClient(secret).list();
  } catch {
    initial = null; // core unreachable or not wired: say so, never show stale or guessed values
  }
  return (
    <section>
      <h1>Settings</h1>
      <p>
        Business rules the super admin can change without a release (A-50). Every change shows the
        current and the new value, needs a reason and is audit-logged. Plans, tiers, zones,
        templates, policies and payment providers are managed on their own screens.
      </p>
      {initial ? (
        <SettingsEditor initial={initial} />
      ) : (
        <p role="alert">
          The settings service is not reachable right now. Nothing was changed; try again shortly.
        </p>
      )}
    </section>
  );
}
