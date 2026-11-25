import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

/// A03: protected routes need a session, so tests that open a tab are
/// signed in as [roles] (default: the app's own role).
Widget app(
  AppSpec spec, {
  double textScale = 1,
  String initial = '/',
  List<String>? roles,
}) => ProviderScope(
  overrides: [
    if (roles != null || initial != '/')
      authServiceProvider.overrideWithValue(
        FakeAuthService()..signInAs(roles ?? [spec.id.wire]),
      ),
  ],
  child: MediaQuery(
    data: MediaQueryData(textScaler: TextScaler.linear(textScale)),
    child: HsApp(router: buildAppRouter(spec, initialLocation: initial)),
  ),
);

void main() {
  testWidgets('parent boots to themed welcome screen', (t) async {
    await t.pumpWidget(app(AppSpec.parent));
    await t.pumpAndSettle();
    expect(find.text('Welcome to Home School'), findsOneWidget);
    expect(find.text('Environment: dev'), findsOneWidget);
    final button = t.widget<FilledButton>(find.byType(FilledButton));
    expect(button.onPressed, isNotNull);
    expect(
      t.getSize(find.byType(FilledButton)).height,
      greaterThanOrEqualTo(48),
    );
  });

  testWidgets('parent tabs follow Design Brief and navigate', (t) async {
    await t.pumpWidget(app(AppSpec.parent, initial: '/home'));
    await t.pumpAndSettle();
    for (final l in ['Home', 'Sessions', 'Progress', 'Billing', 'Account']) {
      expect(find.text(l), findsWidgets);
    }
    await t.tap(find.byType(NavigationDestination).at(3));
    await t.pumpAndSettle();
    expect(find.text('Billing will appear here.'), findsOneWidget);
  });

  testWidgets('teacher tabs follow Design Brief', (t) async {
    await t.pumpWidget(app(AppSpec.teacher, initial: '/today'));
    await t.pumpAndSettle();
    for (final l in ['Today', 'Schedule', 'Earnings', 'Growth', 'Account']) {
      expect(find.text(l), findsWidgets);
    }
  });

  for (final spec in [AppSpec.parent, AppSpec.teacher]) {
    testWidgets('no overflow at 130% font on a small phone', (t) async {
      t.view.physicalSize = const Size(360, 640);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.reset);
      await t.pumpWidget(app(spec, textScale: 1.3));
      await t.pumpAndSettle();
      expect(
        MediaQuery.textScalerOf(t.element(find.byType(FilledButton))).scale(10),
        13,
      );
      expect(t.takeException(), isNull);
      await t.tap(find.text('Get started'));
      await t.pumpAndSettle();
      expect(t.takeException(), isNull);
    });
  }

  testWidgets('offline queue count provider reflects database', (t) async {
    final db = AppDatabase(NativeDatabase.memory());
    addTearDown(db.close);
    final c = ProviderContainer(
      overrides: [offlineQueueProvider.overrideWithValue(OfflineQueue(db))],
    );
    addTearDown(c.dispose);
    expect(await c.read(offlineQueueProvider).count(), 0);
  });
}
