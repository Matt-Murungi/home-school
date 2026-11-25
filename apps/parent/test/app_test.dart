import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';
import 'package:home_school_parent/app.dart';

void main() {
  testWidgets('boots to themed welcome screen', (t) async {
    await t.pumpWidget(const ProviderScope(child: ParentApp()));
    await t.pumpAndSettle();
    expect(find.text('Welcome to Home School'), findsOneWidget);
    expect(
      find.text("Find a trusted teacher and follow your child's progress."),
      findsOneWidget,
    );
    final app = t.widget<MaterialApp>(find.byType(MaterialApp));
    expect(app.theme!.colorScheme.primary, HsColors.primary);
  });

  testWidgets(
    'get started opens phone sign-in, a restored session opens Home',
    (t) async {
      await t.pumpWidget(const ProviderScope(child: ParentApp()));
      await t.pumpAndSettle();
      await t.tap(find.text('Get started'));
      await t.pumpAndSettle();
      expect(find.text('Enter your phone number'), findsOneWidget);
      expect(find.text('+256'), findsOneWidget);

      await t.pumpWidget(const SizedBox());
      await t.pumpWidget(
        const ProviderScope(child: ParentApp(initialLocation: '/home')),
      );
      await t.pumpAndSettle();
      expect(find.text('Home will appear here.'), findsOneWidget);
    },
  );

  testWidgets('130% font on a small phone does not overflow', (t) async {
    t.view.physicalSize = const Size(360, 640);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.reset);
    await t.pumpWidget(
      ProviderScope(
        child: MediaQuery(
          data: const MediaQueryData(textScaler: TextScaler.linear(1.3)),
          child: const ParentApp(),
        ),
      ),
    );
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

  test('Android minSdk is 26 so Android 8 devices can install', () {
    final gradle = File('android/app/build.gradle.kts').readAsStringSync();
    expect(RegExp(r'minSdk\s*=\s*26').hasMatch(gradle), isTrue);
  });

  test('main wires the parent role and the core service URL (A01)', () {
    {
      final main = File('lib/main.dart').readAsStringSync();
      expect(main, contains('AppRole.parent'));
      expect(main, contains('config.coreUrl'));
      expect(main, contains('refreshSession'));
      expect(main, isNot(contains('AppRole.teacher')));
    }
  });

  test('no secrets in app sources', () {
    final bad = RegExp(
      r'(api[_-]?key|secret|password)\s*[:=]\s*["\x27][^"\x27]+',
      caseSensitive: false,
    );
    for (final f in Directory(
      'lib',
    ).listSync(recursive: true).whereType<File>()) {
      expect(bad.hasMatch(f.readAsStringSync()), isFalse, reason: f.path);
    }
  });
}
