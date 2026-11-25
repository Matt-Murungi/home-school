"""Row and file permissions for all roles (M05 #40, SR 6.2, ADR 0012).

- `rules`      `desired_permissions` / `desired_file_permissions` (pure, one place)
- `context`    the business facts the rules ask about (assignments, windows, cases, guardians)
- `service`    `PermissionService`: recompute and apply the difference on events
- `jobs`       address-revoke, permission-reconcile, permission-audit (F04 schedule)
- `fake`       permission-aware fake Appwrite used by the role-played tests
- `matrixdoc`  the matrix in docs/ops/permissions.md, generated from `rules.SPECS`
"""
