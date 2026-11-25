import type { EnvLabel } from "@/lib/config";

export function EnvBadge({ label }: { label: EnvLabel }) {
  return (
    <span className="env-badge" data-testid="env-badge" data-env={label.toLowerCase()}>
      {label}
    </span>
  );
}
