import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart' show Override;
import 'package:flutter_test/flutter_test.dart' show addTearDown;
import 'package:home_school_shared/home_school_shared.dart';
import 'package:home_school_teacher/application/data/draft_database.dart';
import 'package:home_school_teacher/application/data/draft_store.dart';
import 'package:home_school_teacher/application/data/fakes.dart';
import 'package:home_school_teacher/application/state/application_controller.dart';
import 'package:home_school_teacher/application/ui/application_screen.dart';
import 'package:home_school_teacher/application/ui/photo.dart';
import 'package:home_school_teacher/l10n/gen/teacher_strings.dart';

/// One "phone": a local Drift database, a fake core service shared between
/// phones, and a provider container.
class Phone {
  Phone({
    DraftDatabase? db,
    FakeApplicationApi? api,
    this.autosave = const Duration(milliseconds: 5),
    this.retry = const Duration(milliseconds: 20),
    this.keys = const [
      'key-aaaaaaaaaaaaaaaa',
      'key-bbbbbbbbbbbbbbbb',
      'key-cccccccccccccccc',
    ],
  }) : db = db ?? DraftDatabase(NativeDatabase.memory()),
       api = api ?? FakeApplicationApi();

  final DraftDatabase db;
  final FakeApplicationApi api;
  final Duration autosave;
  final Duration retry;
  final List<String> keys;
  final photo = FakePhotoPicker();
  int _k = 0;

  DraftStore get store =>
      DraftStore(db, clock: () => DateTime.utc(2026, 11, 25, 9));

  List<Override> get overrides => [
    applicationApiProvider.overrideWithValue(api),
    referenceSourceProvider.overrideWithValue(api),
    draftStoreProvider.overrideWithValue(store),
    applicationUserIdProvider.overrideWithValue(() async => 'teacher_one'),
    applicationTodayProvider.overrideWithValue(
      () => DateTime.utc(2026, 11, 25),
    ),
    applicationTimingsProvider.overrideWithValue(
      ApplicationTimings(autosave: autosave, retry: retry),
    ),
    submitKeyFactoryProvider.overrideWithValue(() => keys[_k++ % keys.length]),
    photoPickerProvider.overrideWithValue(photo),
  ];

  /// A new process on the same phone (same database), as after an app kill.
  Phone restart() =>
      Phone(db: db, api: api, autosave: autosave, retry: retry, keys: keys);

  ProviderContainer container() {
    final c = ProviderContainer(overrides: overrides);
    addTearDown(c.dispose);
    return c;
  }
}

Widget screenApp(Phone phone, {double scale = 1.0, VoidCallback? onLeave}) =>
    ProviderScope(
      overrides: phone.overrides,
      child: MaterialApp(
        theme: buildHsTheme(),
        localizationsDelegates: const [
          HsStrings.delegate,
          TeacherStrings.delegate,
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
        supportedLocales: TeacherStrings.supportedLocales,
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context)
              .copyWith(textScaler: TextScaler.linear(scale)),
          child: child!,
        ),
        home: ApplicationScreen(onLeave: onLeave),
      ),
    );

Future<void> settle(Future<void> Function() body) async {
  await body();
}
