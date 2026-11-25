import { requireAccess } from "@/lib/session";
import { PageStub } from "@/components/PageStub";

export default async function SettingsPage() {
  await requireAccess("/settings");
  return <PageStub title="Settings" spec="A-50" />;
}
