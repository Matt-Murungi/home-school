import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

// Shared with the core service (tests/test_roles_model.py) and the admin
// app (tests/roles-fixture.test.ts): docs/ops/roles-matrix.json.
final fixture = jsonDecode(
  File('../../docs/ops/roles-matrix.json').readAsStringSync(),
) as Map<String, Object?>;

List<List<String>> subsets(List<String> items) => [
  for (var mask = 0; mask < (1 << items.length); mask++)
    [
      for (var i = 0; i < items.length; i++)
        if (mask & (1 << i) != 0) items[i],
    ],
];

void main() {
  test('role lists and app tables equal the shared fixture', () {
    expect(allRoleLabels, (fixture['roles'] as List).cast<String>());
    expect(staffRoleLabels, (fixture['staff_roles'] as List).cast<String>());
    final apps = fixture['apps'] as Map<String, Object?>;
    for (final a in AppId.values) {
      expect(a.admits, ((apps[a.wire]) as List).cast<String>().toSet());
    }
  });

  test('labels are alphanumeric lower case', () {
    for (final r in allRoleLabels) {
      expect(RegExp(r'^[a-z0-9]+$').hasMatch(r), isTrue, reason: r);
    }
  });

  test('label cases from the shared fixture', () {
    for (final c in (fixture['label_cases'] as List).cast<Map>()) {
      final labels = (c['labels'] as List).cast<String>();
      final roles = (c['roles'] as List).cast<String>();
      expect(cleanLabels(labels), roles.toSet(), reason: '$labels');
    }
  });

  test('malformed label values grant nothing', () {
    for (final junk in [
      null,
      5,
      'opsadmin',
      {'opsadmin': 1},
    ]) {
      expect(cleanLabels(junk), isEmpty, reason: '$junk');
    }
    expect(cleanLabels([null, 7, 'teacher', {}]), {'teacher'});
  });

  test('every role combination against every app matches the fixture', () {
    final apps = fixture['apps'] as Map<String, Object?>;
    for (final roles in subsets(allRoleLabels)) {
      for (final a in AppId.values) {
        final want = roles.any(((apps[a.wire]) as List).contains);
        expect(appAdmits(a, roles), want, reason: '${a.wire} $roles');
      }
    }
  });

  test('decisions: admit, wrong app (with the right app), suspended, none', () {
    expect(decideApp(AppId.parent, ['parent']).kind, AppDecisionKind.admit);
    expect(decideApp(AppId.parent, ['guardian']).admitted, isTrue);
    expect(
      decideApp(AppId.parent, ['teacher']),
      const AppDecision(AppDecisionKind.wrongApp, AppId.teacher),
    );
    expect(
      decideApp(AppId.teacher, ['guardian']),
      const AppDecision(AppDecisionKind.wrongApp, AppId.parent),
    );
    expect(
      decideApp(AppId.teacher, ['opsadmin']),
      const AppDecision(AppDecisionKind.wrongApp, AppId.admin),
    );
    expect(
      decideApp(AppId.teacher, ['parent', 'teacher']).kind,
      AppDecisionKind.admit,
    );
    expect(
      decideApp(AppId.teacher, ['teacher'], AccountStatus.suspended).kind,
      AppDecisionKind.suspended,
    );
    expect(
      decideApp(AppId.teacher, ['parent'], AccountStatus.suspended).kind,
      AppDecisionKind.wrongApp,
    );
    expect(decideApp(AppId.parent, []).kind, AppDecisionKind.noRole);
    expect(decideApp(AppId.parent, ['ops_admin']).kind, AppDecisionKind.noRole);
    expect(
      decideApp(AppId.parent, ['parent'], AccountStatus.deactivated).kind,
      AppDecisionKind.noRole,
    );
  });

  test('every decision agrees with the core service table (all subsets)', () {
    for (final roles in subsets(allRoleLabels)) {
      for (final app in [AppId.parent, AppId.teacher]) {
        final d = decideApp(app, roles);
        if (roles.isEmpty) {
          expect(d.kind, AppDecisionKind.noRole);
        } else if (appAdmits(app, roles)) {
          expect(d.kind, AppDecisionKind.admit);
        } else {
          expect(d.kind, AppDecisionKind.wrongApp);
          expect(d.suggestedApp, isNotNull);
        }
      }
    }
  });

  test('account status words', () {
    expect(accountStatusFrom('suspended'), AccountStatus.suspended);
    expect(accountStatusFrom('deleted'), AccountStatus.deactivated);
    expect(accountStatusFrom('deactivated'), AccountStatus.deactivated);
    expect(accountStatusFrom('active'), AccountStatus.active);
    expect(accountStatusFrom(null), AccountStatus.active);
  });
}
