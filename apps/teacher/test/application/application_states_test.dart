import 'package:drift/drift.dart' show driftRuntimeOptions;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_teacher/application/data/fakes.dart';
import 'package:home_school_teacher/application/model/reference.dart';
import 'package:home_school_teacher/application/state/application_controller.dart';
import 'package:home_school_teacher/application/ui/application_screen.dart';

import 'harness.dart';

Future<ProviderContainer> open(
  WidgetTester t,
  Phone phone, {
  double scale = 1,
  int step = 0,
  bool complete = true,
}) async {
  t.view.physicalSize = const Size(360, 2600);
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.reset);
  if (complete) phone.api.externalEdit(completeDraft());
  phone.api.step = step;
  await t.pumpWidget(screenApp(phone, scale: scale));
  await t.pump(const Duration(milliseconds: 100));
  return ProviderScope.containerOf(t.element(find.byType(ApplicationScreen)));
}

/// Tall test screens show whole steps, so nothing needs scrolling.
Future<void> reveal(WidgetTester t, Finder f) async {
  await t.pump(const Duration(milliseconds: 50));
}

Future<void> tp(WidgetTester t, Finder f) async {
  await reveal(t, f);
  await t.tap(f.first);
  await t.pump(const Duration(milliseconds: 50));
}

void main() {
  driftRuntimeOptions.dontWarnAboutMultipleDatabases = true;
  late Phone phone;
  setUp(() => phone = Phone());
  tearDown(() async {
    await Future<void>.delayed(const Duration(milliseconds: 40));
    await phone.db.close();
  });

  group('T-08 review and submit', () {
    testWidgets(
      'a complete application shows every section ticked and the submit button',
      (t) async {
        await open(t, phone, step: 5);
        expect(find.text('Everything needed is filled in.'), findsOneWidget);
        expect(find.byIcon(Icons.check_circle), findsNWidgets(5));
        expect(find.text('Grace Nakato'), findsOneWidget);
        expect(find.text('Submit application'), findsOneWidget);
      },
    );

    testWidgets(
      'an incomplete application lists the sections still to do, with edit links',
      (t) async {
        final c = await open(t, phone, step: 5, complete: false);
        expect(find.textContaining('Still to do'), findsOneWidget);
        expect(find.byIcon(Icons.error_outline), findsWidgets);
        await tp(t, find.text('Edit').at(3));
        expect(c.read(applicationControllerProvider).step, 3);
        expect(find.text('Referees'), findsWidgets);
      },
    );

    testWidgets('submitting needs the declaration', (t) async {
      await open(t, phone, step: 5);
      await tp(t, find.text('Submit application'));
      expect(
        find.text('Please tick the declaration to submit.'),
        findsOneWidget,
      );
      expect(phone.api.submitCount, 0);
    });

    testWidgets('submit sends it once and shows the submitted screen', (
      t,
    ) async {
      await open(t, phone, step: 5);
      await tp(t, find.byType(Checkbox));
      await tp(t, find.text('Submit application'));
      await t.pump(const Duration(milliseconds: 200));
      expect(phone.api.submitCount, 1);
      expect(phone.api.status, 'submitted');
      expect(find.text('Application submitted'), findsOneWidget);
      expect(find.text('Go to the app'), findsOneWidget);
    });

    testWidgets(
      'offline: the teacher is told it will be sent later, and the button is not offered twice',
      (t) async {
        final c = await open(t, phone, step: 5);
        phone.api.offline = true;
        await tp(t, find.byType(Checkbox));
        await tp(t, find.text('Submit application'));
        await t.pump(const Duration(milliseconds: 100));
        expect(
          find.textContaining('sent as soon as you are online'),
          findsOneWidget,
        );
        expect(
          c.read(applicationControllerProvider).submit,
          SubmitPhase.queuedOffline,
        );
        phone.api.offline = false;
        await t.pump(const Duration(milliseconds: 200));
        await t.pump(const Duration(milliseconds: 200));
        expect(phone.api.submitCount, 1);
        expect(find.text('Application submitted'), findsOneWidget);
      },
    );

    testWidgets('server-side validation failures are listed in plain words', (
      t,
    ) async {
      final c = await open(t, phone, step: 5);
      // The server knows the telephone number is the applicant's own.
      await t.pump(const Duration(milliseconds: 100));
      expect(c.read(applicationControllerProvider).reference, isNotNull);
      // The zone list changed on the server after this phone downloaded it.
      phone.api.reference = const ReferenceData(
        curricula: [],
        zones: [ZoneOption('zone_kira', 'Kira')],
      );
      phone.api.reference = ReferenceData(
        curricula: sampleReference().curricula,
        zones: const [ZoneOption('zone_kira', 'Kira')],
      );
      await tp(t, find.byType(Checkbox));
      await tp(t, find.text('Submit application'));
      await t.pump(const Duration(milliseconds: 200));
      expect(find.text('Please fix these before submitting:'), findsOneWidget);
      expect(find.textContaining('Personal details'), findsWidgets);
      expect(phone.api.submitCount, 0);
    });
  });

  group('status after submitting', () {
    testWidgets('a submitted application opens straight to the status screen', (
      t,
    ) async {
      phone.api
        ..externalEdit(completeDraft())
        ..status = 'under_review';
      await t.pumpWidget(screenApp(phone));
      await t.pump(const Duration(milliseconds: 100));
      expect(find.text('Your application is being reviewed'), findsOneWidget);
      expect(find.text('Next'), findsNothing);
    });

    testWidgets(
      'not approved: reapplying is offered only after the waiting period',
      (t) async {
        phone.api
          ..externalEdit(completeDraft())
          ..status = 'not_approved'
          ..reopenAt = DateTime.utc(2027, 2, 1);
        await t.pumpWidget(screenApp(phone));
        await t.pump(const Duration(milliseconds: 100));
        expect(find.text('Application not approved this time'), findsOneWidget);
        expect(find.text('Apply again'), findsNothing);
        expect(find.textContaining('You can apply again from'), findsOneWidget);
        await t.pumpWidget(const SizedBox());
        phone.api.today = DateTime.utc(2027, 2, 2);
        await t.pumpWidget(screenApp(phone.restart()));
        await t.pump(const Duration(milliseconds: 100));
        await tp(t, find.text('Apply again'));
        await t.pump(const Duration(milliseconds: 100));
        expect(find.text('Step 1 of 6'), findsOneWidget);
      },
    );
  });

  group('sync banner, offline and conflict', () {
    testWidgets('shows saved, then offline with a retry', (t) async {
      final c = await open(t, phone, complete: false);
      expect(find.textContaining('Saved. You can leave'), findsOneWidget);
      phone.api.offline = true;
      c
          .read(applicationControllerProvider.notifier)
          .update(
            (d) => d.copyWith(personal: d.personal.copyWith(fullName: 'Off')),
          );
      await t.pump(const Duration(milliseconds: 60));
      expect(find.textContaining('Saved on this phone'), findsOneWidget);
      expect(find.text('Try again'), findsOneWidget);
      phone.api.offline = false; // the phone retries on its own
      await t.pump(const Duration(milliseconds: 100));
      await t.pump(const Duration(milliseconds: 100));
      expect(find.textContaining('Saved. You can leave'), findsOneWidget);
    });

    testWidgets('a conflicting server draft asks which version to keep', (
      t,
    ) async {
      final c = await open(t, phone, complete: false);
      final n = c.read(applicationControllerProvider.notifier);
      n.update(
        (d) => d.copyWith(personal: d.personal.copyWith(fullName: 'Phone')),
      );
      await t.pump(const Duration(milliseconds: 100));
      phone.api.offline = true;
      n.update(
        (d) =>
            d.copyWith(personal: d.personal.copyWith(fullName: 'Phone edit')),
      );
      await t.pump(const Duration(milliseconds: 100));
      phone.api.externalEdit(completeDraft());
      phone.api.offline = false;
      await n.retryNow();
      await t.pump(const Duration(milliseconds: 100));
      expect(find.text('Two versions of your application'), findsOneWidget);
      await tp(t, find.text('Use the saved version'));
      await t.pump(const Duration(milliseconds: 100));
      expect(find.text('Two versions of your application'), findsNothing);
      expect(
        t
            .widget<TextFormField>(
              find.widgetWithText(TextFormField, 'Full name'),
            )
            .initialValue,
        'Grace Nakato',
      );
    });
  });

  group('accessibility and low-end phones', () {
    for (final scale in [1.3, 2.0]) {
      testWidgets(
        'every step renders without overflow at ${(scale * 100).round()}% text',
        (t) async {
          final c = await open(t, phone, scale: scale);
          for (var i = 0; i < 6; i++) {
            c.read(applicationControllerProvider.notifier).goToStep(i);
            await t.pump(const Duration(milliseconds: 200));
            expect(t.takeException(), isNull, reason: 'step $i at $scale');
            final scroll = find.byType(Scrollable).first;
            for (var k = 0; k < 6; k++) {
              await t.drag(scroll, const Offset(0, -400));
              await t.pump(const Duration(milliseconds: 100));
              expect(
                t.takeException(),
                isNull,
                reason: 'step $i scroll $k at $scale',
              );
            }
          }
        },
      );
    }

    testWidgets('buttons, chips and steppers are at least 48 dp', (t) async {
      final c = await open(t, phone);
      for (var i = 0; i < 6; i++) {
        c.read(applicationControllerProvider.notifier).goToStep(i);
        await t.pump(const Duration(milliseconds: 200));
        for (final type in [
          FilledButton,
          OutlinedButton,
          TextButton,
          IconButton,
          ChoiceChip,
          FilterChip,
        ]) {
          for (final e in find.byType(type).evaluate()) {
            final size = t.getSize(find.byWidget(e.widget));
            expect(
              size.height,
              greaterThanOrEqualTo(47.9),
              reason: '$type on step $i',
            );
            expect(
              size.width,
              greaterThanOrEqualTo(47.9),
              reason: '$type on step $i',
            );
          }
        }
      }
    });

    testWidgets(
      'no heavy images: the photo preview is decoded at thumbnail size',
      (t) async {
        await open(t, phone);
        expect(find.byType(Image), findsWidgets);
        final img = t.widget<Image>(find.byType(Image).first);
        expect((img.image as ResizeImage).width, 192);
      },
    );
  });
}
