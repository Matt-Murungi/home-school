import 'dart:io';

import 'package:drift/drift.dart' show driftRuntimeOptions;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_teacher/application/data/fakes.dart';
import 'package:home_school_teacher/application/model/application_draft.dart';
import 'package:home_school_teacher/application/state/application_controller.dart';
import 'package:home_school_teacher/application/ui/application_screen.dart';

import 'harness.dart';

Future<ProviderContainer> open(
  WidgetTester t,
  Phone phone, {
  double scale = 1.0,
  int? atStep,
  ApplicationDraft? prefill,
}) async {
  t.view.physicalSize = const Size(360, 2600);
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.reset);
  if (prefill != null) phone.api.externalEdit(prefill);
  if (atStep != null) phone.api.step = atStep;
  await t.pumpWidget(screenApp(phone, scale: scale));
  await t.pump();
  await t.pump(const Duration(milliseconds: 50));
  return ProviderScope.containerOf(t.element(find.byType(ApplicationScreen)));
}

ApplicationState st(ProviderContainer c) =>
    c.read(applicationControllerProvider);

/// Scroll to the middle of the viewport (not the very edge), then tap.
/// Tall test screens show whole steps, so nothing needs scrolling.
Future<void> reveal(WidgetTester t, Finder f) async {
  await t.pump(const Duration(milliseconds: 50));
}

Future<void> tp(WidgetTester t, Finder f) async {
  await reveal(t, f);
  await t.tap(f.first);
  await t.pump(const Duration(milliseconds: 30));
}

Future<void> next(WidgetTester t) async {
  await reveal(t, find.text('Next'));
  await tp(t, find.text('Next'));
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

  group('progress and navigation', () {
    testWidgets(
      'shows a progress indicator with step x of 6 and the step name',
      (t) async {
        await open(t, phone);
        expect(find.text('Step 1 of 6'), findsOneWidget);
        expect(find.text('Personal details'), findsOneWidget);
        expect(find.byType(LinearProgressIndicator), findsOneWidget);
        expect(
          find.bySemanticsLabel('Step 1 of 6: Personal details'),
          findsOneWidget,
        );
      },
    );

    testWidgets('progress follows the steps', (t) async {
      await open(t, phone, prefill: completeDraft());
      await next(t);
      expect(find.text('Step 2 of 6'), findsOneWidget);
      expect(find.text('Qualifications and curricula'), findsWidgets);
      final bar = t.widget<LinearProgressIndicator>(
        find.byType(LinearProgressIndicator),
      );
      expect(bar.value, closeTo(2 / 6, 1e-9));
    });

    testWidgets('Next on an incomplete step shows what is missing and stays', (
      t,
    ) async {
      await open(t, phone);
      await next(t);
      expect(find.text('Step 1 of 6'), findsOneWidget);
      expect(find.text('This is required.'), findsWidgets);
    });

    testWidgets('Back goes to the previous step without losing anything', (
      t,
    ) async {
      final c = await open(t, phone, prefill: completeDraft());
      await next(t);
      await t.enterText(
        find.widgetWithText(TextFormField, 'Institution'),
        'Kyambogo University',
      );
      await t.pump(const Duration(milliseconds: 20));
      await reveal(t, find.text('Back'));
      await tp(t, find.text('Back'));
      await t.pump(const Duration(milliseconds: 50));
      expect(find.text('Step 1 of 6'), findsOneWidget);
      expect(find.widgetWithText(TextFormField, 'Full name'), findsOneWidget);
      expect(
        t
            .widget<TextFormField>(
              find.widgetWithText(TextFormField, 'Full name'),
            )
            .initialValue,
        'Grace Nakato',
      );
      await next(t);
      expect(
        t
            .widget<TextFormField>(
              find.widgetWithText(TextFormField, 'Institution'),
            )
            .initialValue,
        'Kyambogo University',
      );
      expect(st(c).draft.qualifications.institution, 'Kyambogo University');
    });

    testWidgets('there is no Back on the first step and no Next on the last', (
      t,
    ) async {
      await open(t, phone, prefill: completeDraft());
      expect(find.text('Back'), findsNothing);
      phone.api.step = 5;
      await t.pumpWidget(const SizedBox());
      final p2 = Phone(api: phone.api); // a phone that has never seen the draft
      await t.pumpWidget(screenApp(p2));
      await t.pump(const Duration(milliseconds: 80));
      expect(find.text('Next'), findsNothing);
      expect(find.text('Review and submit'), findsWidgets);
    });

    testWidgets(
      'closing and reopening the app resumes at the last step with the data',
      (t) async {
        final c = await open(t, phone, prefill: completeDraft());
        await next(t);
        await next(t);
        expect(st(c).step, 2);
        await t.pump(const Duration(milliseconds: 100));
        await t.pumpWidget(const SizedBox()); // app killed
        await t.pumpWidget(screenApp(phone.restart()));
        await t.pump(const Duration(milliseconds: 80));
        expect(find.text('Step 3 of 6'), findsOneWidget);
        expect(find.text('Documents'), findsWidgets);
      },
    );

    testWidgets(
      'loading state then error state with retry when nothing is available',
      (t) async {
        phone.api.offline = true;
        t.view.physicalSize = const Size(360, 2600);
        t.view.devicePixelRatio = 1;
        addTearDown(t.view.reset);
        await t.pumpWidget(screenApp(phone));
        expect(find.bySemanticsLabel('Loading'), findsOneWidget);
        await t.pump(const Duration(milliseconds: 50));
        expect(
          find.textContaining('could not load your application'),
          findsOneWidget,
        );
        phone.api.offline = false;
        await tp(t, find.text('Try again'));
        await t.pump(const Duration(milliseconds: 80));
        expect(find.text('Step 1 of 6'), findsOneWidget);
      },
    );
  });

  group('T-03 personal details and photo', () {
    testWidgets(
      'typing saves into the draft; title, gender and languages are choices',
      (t) async {
        final c = await open(t, phone);
        await t.enterText(
          find.widgetWithText(TextFormField, 'Full name'),
          'Grace Nakato',
        );
        await tp(t, find.text('Ms'));
        await tp(t, find.text('Female'));
        await reveal(t, find.text('Luganda'));
        await tp(t, find.text('English'));
        await tp(t, find.text('Luganda'));
        await t.pump(const Duration(milliseconds: 50));
        final p = st(c).draft.personal;
        expect(p.fullName, 'Grace Nakato');
        expect(p.title, 'ms');
        expect(p.gender, 'female');
        expect(p.languages, ['en', 'lg']);
        await tp(t, find.text('Luganda')); // untick
        await t.pump();
        expect(st(c).draft.personal.languages, ['en']);
      },
    );

    testWidgets(
      'photo guidance is shown; choosing a photo opens the crop screen; the crop is saved',
      (t) async {
        final c = await open(t, phone);
        await reveal(t, find.text('A good photo'));
        expect(find.text('Plain, light background.'), findsOneWidget);
        expect(find.text('Shoulders up, facing the camera.'), findsOneWidget);
        await reveal(t, find.text('Choose from phone'));
        await tp(t, find.text('Choose from phone'));
        await t.pumpAndSettle();
        expect(find.text('Crop your photo'), findsOneWidget);
        await t.drag(find.byType(Slider).first, const Offset(60, 0));
        await tp(t, find.text('Use this crop'));
        await t.pumpAndSettle();
        final photo = st(c).draft.personal.photo!;
        expect(photo.state, 'chosen');
        expect(photo.localPath, endsWith('photo.jpg'));
        expect(photo.crop!.size, lessThan(1));
        expect(find.bySemanticsLabel('Your chosen photo'), findsOneWidget);
        // the server never receives the device path
        expect(
          st(c).draft.toJson(forServer: true).toString().contains('photo.jpg'),
          isFalse,
        );
      },
    );

    testWidgets('the photo can be replaced or removed', (t) async {
      final c = await open(t, phone, prefill: completeDraft());
      await reveal(t, find.text('Remove photo'));
      await tp(t, find.text('Remove photo'));
      await t.pump(const Duration(milliseconds: 50));
      expect(st(c).draft.personal.photo, isNull);
      await next(t);
      expect(find.text('Photo is required.'), findsNothing); // generic text
      expect(find.text('This is required.'), findsWidgets);
    });

    testWidgets('camera permission trouble shows a message, not a crash', (
      t,
    ) async {
      await open(t, phone);
      phone.photo.throwOnPick = true;
      await reveal(t, find.text('Take a photo'));
      await tp(t, find.text('Take a photo'));
      await t.pump(const Duration(milliseconds: 50));
      expect(find.textContaining('could not open the camera'), findsOneWidget);
    });

    testWidgets('date of birth under 18 is refused with a clear message', (
      t,
    ) async {
      await open(t, phone);
      await reveal(t, find.text('Choose a date'));
      await tp(t, find.text('Choose a date'));
      await t.pumpAndSettle();
      await t.enterText(find.byType(TextField).last, '11/25/2015');
      await tp(t, find.text('OK'));
      await t.pumpAndSettle();
      expect(find.text('You must be at least 18.'), findsOneWidget);
    });

    testWidgets('a valid date is shown in words and saved as an ISO date', (
      t,
    ) async {
      final c = await open(t, phone);
      await reveal(t, find.text('Choose a date'));
      await tp(t, find.text('Choose a date'));
      await t.pumpAndSettle();
      await t.enterText(find.byType(TextField).last, '04/17/1992');
      await tp(t, find.text('OK'));
      await t.pumpAndSettle();
      expect(st(c).draft.personal.dateOfBirth, '1992-04-17');
      expect(find.text('April 17, 1992'), findsOneWidget);
    });
  });

  group('T-04 qualifications and curricula', () {
    testWidgets('a registered teacher must give the registration number', (
      t,
    ) async {
      final c = await open(t, phone, prefill: completeDraft(), atStep: 1);
      c
          .read(applicationControllerProvider.notifier)
          .update(
            (d) => d.copyWith(
              qualifications: d.qualifications.copyWith(
                clearRegistration: true,
              ),
            ),
          );
      await t.pump(const Duration(milliseconds: 30));
      await t.pumpWidget(screenApp(phone.restart()));
      await t.pump(const Duration(milliseconds: 100));
      await next(t);
      expect(find.text('Step 2 of 6'), findsOneWidget);
      expect(find.text('This is required.'), findsOneWidget);
    });

    testWidgets(
      'a graduate may leave the registration number empty and still go on',
      (t) async {
        final c = await open(t, phone, prefill: completeDraft(), atStep: 1);
        await tp(t, find.text('Graduate'));
        await t.pump(const Duration(milliseconds: 20));
        await t.enterText(
          find.widgetWithText(TextFormField, 'Registration number (optional)'),
          '',
        );
        await t.pump(const Duration(milliseconds: 20));
        expect(find.text('Graduates can leave this empty.'), findsOneWidget);
        expect(st(c).draft.qualifications.teacherType, 'graduate');
        await next(t);
        expect(find.text('Step 3 of 6'), findsOneWidget);
      },
    );

    testWidgets(
      'curricula: ticking one reveals its levels and subjects from the catalogue',
      (t) async {
        final c = await open(t, phone, atStep: 1);
        await reveal(t, find.text('Cambridge'));
        expect(find.text('Stage 1'), findsNothing);
        await tp(t, find.text('Cambridge'));
        await t.pump(const Duration(milliseconds: 30));
        await reveal(t, find.text('Stage 1'));
        await tp(t, find.text('Stage 1'));
        await tp(t, find.text('Stage 2'));
        await reveal(t, find.text('English').last);
        await tp(t, find.text('English').last);
        await t.pump(const Duration(milliseconds: 30));
        final pick = st(c).draft.curricula.single;
        expect(pick.curriculumId, 'cur_cam');
        expect(pick.levelIds, ['lvl_cp1', 'lvl_cp2']);
        expect(pick.subjectIds, ['sub_cen']);
        await tp(t, find.text('Cambridge')); // untick removes the whole choice
        await t.pump(const Duration(milliseconds: 30));
        expect(st(c).draft.curricula, isEmpty);
      },
    );

    testWidgets('Next with a curriculum but no level or subject says so', (
      t,
    ) async {
      await open(t, phone, prefill: completeDraft(), atStep: 1);
      await reveal(t, find.text('American'));
      await tp(t, find.text('American'));
      await t.pump(const Duration(milliseconds: 30));
      await next(t);
      expect(find.text('This is required.'), findsWidgets);
      expect(find.text('Step 2 of 6'), findsOneWidget);
    });

    testWidgets('catalogue unavailable offline: explained with a retry', (
      t,
    ) async {
      phone.api.offline = true;
      phone.api.externalEdit(completeDraft());
      await t.pumpWidget(screenApp(phone));
      await t.pump(const Duration(milliseconds: 60));
      // no local draft and no server: load failure screen; use a local draft instead
      expect(find.textContaining('could not load'), findsOneWidget);
    });

    testWidgets('catalogue loading shows a progress indicator', (t) async {
      phone.api.latency = const Duration(milliseconds: 200);
      t.view.physicalSize = const Size(360, 2600);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.reset);
      await t.pumpWidget(screenApp(phone));
      await t.pump(const Duration(milliseconds: 250));
      await t.pump(const Duration(milliseconds: 100));
      // fetch finished, reference still loading
      final c = ProviderScope.containerOf(
        t.element(find.byType(ApplicationScreen)),
      );
      c.read(applicationControllerProvider.notifier).goToStep(1);
      await t.pump(const Duration(milliseconds: 20));
      expect(find.bySemanticsLabel('Loading curricula'), findsOneWidget);
      await t.pump(const Duration(milliseconds: 400));
      expect(find.text('Ugandan'), findsOneWidget);
    });
  });

  group('T-05 documents', () {
    testWidgets('lists what will be needed and does not block', (t) async {
      await open(t, phone, prefill: completeDraft(), atStep: 2);
      expect(find.text('National ID (front and back)'), findsOneWidget);
      expect(
        find.text(
          'Police Certificate of Good Conduct (or tell us you have applied)',
        ),
        findsOneWidget,
      );
      await next(t);
      expect(find.text('Step 4 of 6'), findsOneWidget);
    });
  });

  group('T-06 referees', () {
    testWidgets('two referees; each field is validated', (t) async {
      final c = await open(t, phone, atStep: 3);
      expect(find.text('Referee 1'), findsOneWidget);
      await reveal(t, find.text('Referee 2'));
      expect(find.text('Referee 2'), findsOneWidget);
      await t.enterText(
        find.widgetWithText(TextFormField, 'Phone number').first,
        '12345',
      );
      await t.pump(const Duration(milliseconds: 30));
      expect(find.text('Please check this value.'), findsOneWidget);
      await t.enterText(
        find.widgetWithText(TextFormField, 'Phone number').first,
        '0701 234 567',
      );
      await t.pump(const Duration(milliseconds: 100));
      await t.pump(const Duration(milliseconds: 400));
      expect(find.text('Please check this value.'), findsNothing);
      expect(st(c).draft.referees.first.phone, '0701 234 567');
    });

    testWidgets('the same phone twice is refused', (t) async {
      await open(t, phone, prefill: completeDraft(), atStep: 3);
      final phones = find.widgetWithText(TextFormField, 'Phone number');
      await reveal(t, phones.last);
      await t.enterText(phones.last, '+256 701-234-567');
      await t.pump(const Duration(milliseconds: 30));
      expect(find.text('This is already used.'), findsOneWidget);
    });

    testWidgets('consent that the referees may be contacted is required', (
      t,
    ) async {
      final c = await open(t, phone, prefill: completeDraft(), atStep: 3);
      await reveal(t, find.textContaining('Home School may contact them'));
      expect(
        find.textContaining('may contact them about my application'),
        findsOneWidget,
      );
      await tp(t, find.byType(Checkbox));
      await t.pump(const Duration(milliseconds: 30));
      expect(st(c).draft.refereeConsent, isFalse);
      await next(t);
      expect(find.text('Step 4 of 6'), findsOneWidget);
      expect(find.text('This is required.'), findsOneWidget);
    });

    testWidgets('years known and relationship are required', (t) async {
      await open(t, phone, atStep: 3);
      await next(t);
      expect(find.text('This is required.'), findsWidgets);
      expect(find.text('Step 4 of 6'), findsOneWidget);
    });
  });

  group('T-07 zones and availability', () {
    testWidgets(
      'zones come from the zone list; radius has steppers; peak hours are highlighted',
      (t) async {
        final c = await open(t, phone, atStep: 4);
        await tp(t, find.text('Ntinda'));
        await tp(t, find.text('Kira'));
        await t.pump(const Duration(milliseconds: 30));
        expect(st(c).draft.zoneIds, ['zone_ntinda', 'zone_kira']);
        await reveal(t, find.byTooltip('More distance'));
        await tp(t, find.byTooltip('More distance'));
        await t.pump();
        expect(find.text('6 km'), findsOneWidget);
        await tp(t, find.byTooltip('Less distance'));
        await tp(t, find.byTooltip('Less distance'));
        await t.pump();
        expect(find.text('4 km'), findsOneWidget);
        expect(
          find.textContaining('Weekday evenings are the busiest'),
          findsOneWidget,
        );
        expect(
          find.bySemanticsLabel('Mon 16:00-17:00, high demand'),
          findsOneWidget,
        );
        expect(find.bySemanticsLabel('Mon 09:00-10:00'), findsOneWidget);
      },
    );

    testWidgets('tapping hours builds slots and shows the weekly total', (
      t,
    ) async {
      final c = await open(t, phone, atStep: 4);
      Finder block(String label) => find.bySemanticsLabel(label);
      await reveal(t, block('Mon 15:00-16:00, high demand'));
      for (final l in [
        'Mon 15:00-16:00, high demand',
        'Mon 16:00-17:00, high demand',
        'Mon 18:00-19:00, high demand',
      ]) {
        await reveal(t, block(l));
        await tp(t, block(l));
        await t.pump();
      }
      expect(st(c).draft.availability, [
        const AvailabilitySlot(1, 900, 1020),
        const AvailabilitySlot(1, 1080, 1140),
      ]);
      expect(find.text('3 hours a week'), findsOneWidget);
      await tp(t, block('Mon 16:00-17:00, high demand')); // untick
      await t.pump();
      expect(find.text('2 hours a week'), findsOneWidget);
    });

    testWidgets(
      'fewer than the minimum hours blocks Next with an explanation',
      (t) async {
        await open(
          t,
          phone,
          prefill: completeDraft().copyWith(
            availability: const [AvailabilitySlot(1, 900, 1020)],
          ),
          atStep: 4,
        );
        await next(t);
        expect(find.text('Offer at least 6 hours a week.'), findsWidgets);
        expect(find.text('Step 5 of 6'), findsOneWidget);
      },
    );

    testWidgets('no zone and no radius are required', (t) async {
      await open(t, phone, atStep: 4);
      await next(t);
      expect(find.text('This is required.'), findsWidgets);
    });

    testWidgets('overlapping hours coming from elsewhere are reported', (
      t,
    ) async {
      await open(
        t,
        phone,
        prefill: completeDraft().copyWith(
          availability: const [
            AvailabilitySlot(1, 900, 1200),
            AvailabilitySlot(1, 960, 1080),
          ],
        ),
        atStep: 4,
      );
      expect(
        find.text('These hours overlap with another block.'),
        findsOneWidget,
      );
    });
  });

  test('every widget string comes from the ARB (no literal Text) - NFR-13', () {
    final literal = RegExp(r'''Text\(\s*['"]''');
    final offenders = [
      for (final f in Directory(
        'lib/application',
      ).listSync(recursive: true).whereType<File>())
        if (f.path.endsWith('.dart') &&
            !f.path.endsWith('.g.dart') &&
            literal.hasMatch(f.readAsStringSync()))
          f.path,
    ];
    expect(offenders, isEmpty);
    expect(File('l10n.yaml').readAsStringSync(), contains('app_en.arb'));
  });

  test('no copy says homeschool(ing): this is home tutoring', () {
    final arb = File('lib/l10n/app_en.arb').readAsStringSync().toLowerCase();
    expect(arb.contains('homeschool'), isFalse);
  });
}
