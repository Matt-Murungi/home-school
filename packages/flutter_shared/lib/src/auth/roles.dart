/// The role model on the client (A03, #45). It mirrors `hs_core.roles.model` in the core service
/// and `apps/admin/src/lib/roles.ts`; all three are tested against
/// `docs/ops/roles-matrix.json`. This decides only what the app SHOWS: the core service enforces
/// every call from the verified Appwrite account, never from anything the app says.
library;

/// Appwrite user labels, alphanumeric only (ADR 0012). Exact, case-sensitive.
const allRoleLabels = <String>[
  'parent',
  'guardian',
  'teacher',
  'opsadmin',
  'curator',
  'safeguardinglead',
  'superadmin',
];
const staffRoleLabels = <String>[
  'opsadmin',
  'curator',
  'safeguardinglead',
  'superadmin',
];

/// The three clients. `admin` is the web dashboard (not a Flutter app) and
/// only appears here as a suggestion for staff who open a mobile app.
enum AppId {
  parent('parent', {'parent', 'guardian'}),
  teacher('teacher', {'teacher'}),
  admin('admin', {'opsadmin', 'curator', 'safeguardinglead', 'superadmin'});

  const AppId(this.wire, this.admits);

  /// The value used by the core service (`/v1/me/access?app=`).
  final String wire;
  final Set<String> admits;
}

/// Valid labels only: unknown names, case or underscore variants and
/// non-lists grant nothing.
Set<String> cleanLabels(Object? labels) {
  if (labels is! Iterable) return const {};
  return {
    for (final l in labels)
      if (l is String && allRoleLabels.contains(l)) l,
  };
}

bool appAdmits(AppId app, Iterable<String> roles) =>
    roles.any(app.admits.contains);

enum AccountStatus { active, suspended, deactivated }

AccountStatus accountStatusFrom(Object? raw) => switch (raw) {
  'suspended' => AccountStatus.suspended,
  'deactivated' || 'deleted' => AccountStatus.deactivated,
  _ => AccountStatus.active,
};

enum AppDecisionKind { admit, wrongApp, suspended, noRole }

class AppDecision {
  const AppDecision(this.kind, [this.suggestedApp]);
  final AppDecisionKind kind;

  /// For [AppDecisionKind.wrongApp]: the app that does admit this user.
  final AppId? suggestedApp;

  bool get admitted => kind == AppDecisionKind.admit;

  @override
  bool operator ==(Object other) =>
      other is AppDecision &&
      other.kind == kind &&
      other.suggestedApp == suggestedApp;
  @override
  int get hashCode => Object.hash(kind, suggestedApp);
  @override
  String toString() => 'AppDecision($kind, $suggestedApp)';
}

/// What [app] should do with a signed-in user. Same rules as the core's
/// `app_decision`.
AppDecision decideApp(
  AppId app,
  Object? labels, [
  AccountStatus status = AccountStatus.active,
]) {
  final roles = cleanLabels(labels);
  if (status == AccountStatus.deactivated || roles.isEmpty) {
    return const AppDecision(AppDecisionKind.noRole);
  }
  if (!appAdmits(app, roles)) {
    final other = [
      AppId.parent,
      AppId.teacher,
      AppId.admin,
    ].where((a) => a != app && appAdmits(a, roles));
    return AppDecision(
      AppDecisionKind.wrongApp,
      other.isEmpty ? null : other.first,
    );
  }
  if (status == AccountStatus.suspended) {
    return const AppDecision(AppDecisionKind.suspended);
  }
  return const AppDecision(AppDecisionKind.admit);
}
