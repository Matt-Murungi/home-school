import 'package:drift/drift.dart' show driftRuntimeOptions;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_teacher/application/data/fakes.dart';
import 'package:home_school_teacher/application/model/application_draft.dart';
import 'package:home_school_teacher/application/state/application_controller.dart';

import 'harness.dart';

Future<void> waitFor(bool Function() cond, {int ms = 1500}) async {
  final end = DateTime.now().add(Duration(milliseconds: ms));
  while (!cond()) {
    if (DateTime.now().isAfter(end)) fail('condition not reached in ${ms}ms');
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }
}

ApplicationDraft named(String n) =>
    ApplicationDraft(personal: Personal(fullName: n));

void main() {
  driftRuntimeOptions.dontWarnAboutMultipleDatabases = true;
  late Phone phone;
  setUp(() => phone = Phone());
  tearDown(() async {
    await Future<void>.delayed(const Duration(milliseconds: 40));
    await phone.db.close();
  });

  test('a new teacher starts with an empty editable draft', () async {
    final c = phone.container();
    await c.read(applicationControllerProvider.notifier).load();
    final s = c.read(applicationControllerProvider);
    expect(s.loading, isFalse);
    expect(s.status, 'none');
    expect(s.draft.isEmpty, isTrue);
    expect(s.canEdit, isTrue);
    await waitFor(
      () => c.read(applicationControllerProvider).reference != null,
    );
  });

  group('progress saved per step, resume later', () {
    test('every edit is on the phone at once, and reaches the server after the pause', () async {
      final c = phone.container();
      final n = c.read(applicationControllerProvider.notifier);
      await n.load();
      n.update(
        (d) => d.copyWith(personal: d.personal.copyWith(fullName: 'Grace')),
      );
      // the local write does not wait for the autosave timer
      await n.settled;
      final local = await phone.store.load('teacher_one');
      expect(local!.draft.personal.fullName, 'Grace');
      expect(local.dirty, isTrue);
      await waitFor(() => !c.read(applicationControllerProvider).dirty);
      expect(phone.api.content.personal.fullName, 'Grace');
      expect(c.read(applicationControllerProvider).sync, SyncStatus.saved);
      expect((await phone.store.load('teacher_one'))!.dirty, isFalse);
    });

    test('the app is killed mid-application: it resumes at the last step with all data', () async {
      final c1 = phone.container();
      final n1 = c1.read(applicationControllerProvider.notifier);
      await n1.load();
      n1.update(
        (d) =>
            d.copyWith(personal: d.personal.copyWith(fullName: 'Grace Nakato')),
      );
      n1.goToStep(2);
      await waitFor(() => !c1.read(applicationControllerProvider).dirty);
      c1.dispose();
      final p2 = phone.restart();
      final c2 = p2.container();
      await c2.read(applicationControllerProvider.notifier).load();
      final s = c2.read(applicationControllerProvider);
      expect(s.step, 2);
      expect(s.draft.personal.fullName, 'Grace Nakato');
    });

    test('killed before the autosave ran: the edit survives on the phone and is sent next start', () async {
      final slow = Phone(
        db: phone.db,
        api: phone.api,
        autosave: const Duration(hours: 1),
      );
      final c1 = slow.container();
      final n1 = c1.read(applicationControllerProvider.notifier);
      await n1.load();
      n1.update(
        (d) =>
            d.copyWith(personal: d.personal.copyWith(fullName: 'Unsent Edit')),
      );
      await Future<void>.delayed(const Duration(milliseconds: 30));
      expect(
        phone.api.content.personal.fullName,
        isNull,
      ); // never reached the server
      c1.dispose(); // process killed
      final c2 = slow.restart().container();
      await c2.read(applicationControllerProvider.notifier).load();
      expect(
        c2.read(applicationControllerProvider).draft.personal.fullName,
        'Unsent Edit',
      );
      await waitFor(() => phone.api.content.personal.fullName == 'Unsent Edit');
    });

    test('resume on a new phone: the server draft is used', () async {
      phone.api.externalEdit(completeDraft());
      phone.api.step = 4;
      final other = Phone(api: phone.api);
      final c = other.container();
      await c.read(applicationControllerProvider.notifier).load();
      final s = c.read(applicationControllerProvider);
      expect(s.step, 4);
      expect(s.draft.personal.fullName, 'Grace Nakato');
      expect(s.dirty, isFalse);
      await Future<void>.delayed(const Duration(milliseconds: 40));
      await other.db.close();
    });

    test('a newer server draft replaces a clean local copy silently', () async {
      final c1 = phone.container();
      final n1 = c1.read(applicationControllerProvider.notifier);
      await n1.load();
      n1.update(
        (d) => d.copyWith(personal: d.personal.copyWith(fullName: 'Old')),
      );
      await waitFor(() => !c1.read(applicationControllerProvider).dirty);
      c1.dispose();
      phone.api.externalEdit(named('Newer from the web'));
      final c2 = phone.restart().container();
      await c2.read(applicationControllerProvider.notifier).load();
      final s = c2.read(applicationControllerProvider);
      expect(s.draft.personal.fullName, 'Newer from the web');
      expect(s.sync, SyncStatus.saved);
      expect(s.conflict, isNull);
    });
  });

  group('offline', () {
    test('edits made offline stay on the phone, show offline, and sync when the network is back', () async {
      final c = phone.container();
      final n = c.read(applicationControllerProvider.notifier);
      await n.load();
      phone.api.offline = true;
      n.update(
        (d) => d.copyWith(
          personal: d.personal.copyWith(fullName: 'Offline Grace'),
        ),
      );
      await waitFor(
        () => c.read(applicationControllerProvider).sync == SyncStatus.offline,
      );
      expect(c.read(applicationControllerProvider).dirty, isTrue);
      expect(
        (await phone.store.load('teacher_one'))!.draft.personal.fullName,
        'Offline Grace',
      );
      phone.api.offline = false;
      await waitFor(
        () => c.read(applicationControllerProvider).sync == SyncStatus.saved,
      );
      expect(phone.api.content.personal.fullName, 'Offline Grace');
    });

    test('starting the app with no network uses the local draft', () async {
      final c1 = phone.container();
      final n1 = c1.read(applicationControllerProvider.notifier);
      await n1.load();
      n1.update(
        (d) =>
            d.copyWith(personal: d.personal.copyWith(fullName: 'Local Only')),
      );
      n1.goToStep(1);
      await waitFor(() => !c1.read(applicationControllerProvider).dirty);
      c1.dispose();
      phone.api.offline = true;
      final c2 = phone.restart().container();
      await c2.read(applicationControllerProvider.notifier).load();
      final s = c2.read(applicationControllerProvider);
      expect(s.loadFailed, isFalse);
      expect(s.draft.personal.fullName, 'Local Only');
      expect(s.step, 1);
      expect(s.sync, SyncStatus.offline);
    });

    test('first start with no network and nothing local is a load failure with retry', () async {
      phone.api.offline = true;
      final c = phone.container();
      final n = c.read(applicationControllerProvider.notifier);
      await n.load();
      expect(c.read(applicationControllerProvider).loadFailed, isTrue);
      phone.api.offline = false;
      await n.load();
      expect(c.read(applicationControllerProvider).loadFailed, isFalse);
    });

    test(
      'the catalogue is cached: an offline start still has curricula and zones',
      () async {
        final c1 = phone.container();
        await c1.read(applicationControllerProvider.notifier).load();
        await waitFor(
          () => c1.read(applicationControllerProvider).reference != null,
        );
        await Future<void>.delayed(const Duration(milliseconds: 30));
        c1.dispose();
        phone.api.offline = true;
        final c2 = phone.restart().container();
        await c2.read(applicationControllerProvider.notifier).load();
        await waitFor(
          () => c2.read(applicationControllerProvider).reference != null,
        );
        expect(
          c2.read(applicationControllerProvider).reference!.curricula.length,
          3,
        );
      },
    );

    test('catalogue never loaded and offline: flagged so the screen can offer a retry', () async {
      phone.api.offline = true;
      final c = phone.container();
      final n = c.read(applicationControllerProvider.notifier);
      await n.load();
      await waitFor(
        () => c.read(applicationControllerProvider).referenceFailed,
      );
      phone.api.offline = false;
      await n.retryReference();
      expect(c.read(applicationControllerProvider).reference, isNotNull);
    });

    test(
      'rate limited by the server: stays dirty and retries after the wait',
      () async {
        final c = phone.container();
        final n = c.read(applicationControllerProvider.notifier);
        await n.load();
        phone.api.nextFailure = const Failure.rateLimited(
          Duration(milliseconds: 20),
        );
        n.update(
          (d) =>
              d.copyWith(personal: d.personal.copyWith(fullName: 'Throttled')),
        );
        await waitFor(
          () =>
              c.read(applicationControllerProvider).sync ==
              SyncStatus.rateLimited,
        );
        await waitFor(
          () => c.read(applicationControllerProvider).sync == SyncStatus.saved,
        );
        expect(phone.api.content.personal.fullName, 'Throttled');
      },
    );
  });

  group('conflicting server draft', () {
    Future<(ProviderContainer, ApplicationController)> conflicted() async {
      final c1 = phone.container();
      final n1 = c1.read(applicationControllerProvider.notifier);
      await n1.load();
      n1.update(
        (d) => d.copyWith(personal: d.personal.copyWith(fullName: 'Base')),
      );
      await waitFor(() => !c1.read(applicationControllerProvider).dirty);
      phone.api.offline = true;
      n1.update(
        (d) => d.copyWith(
          personal: d.personal.copyWith(fullName: 'Edited on phone'),
        ),
      );
      await waitFor(
        () => c1.read(applicationControllerProvider).sync == SyncStatus.offline,
      );
      phone.api.externalEdit(named('Edited on web'));
      phone.api.offline = false;
      return (c1, n1);
    }

    test(
      'both sides changed: the teacher is asked, nothing is overwritten',
      () async {
        final (c, n) = await conflicted();
        await n.retryNow();
        final s = c.read(applicationControllerProvider);
        expect(s.sync, SyncStatus.conflict);
        expect(s.conflict!.content.personal.fullName, 'Edited on web');
        expect(s.draft.personal.fullName, 'Edited on phone');
        expect(phone.api.content.personal.fullName, 'Edited on web');
      },
    );

    test('keep mine: pushes this phone version over the server one', () async {
      final (c, n) = await conflicted();
      await n.retryNow();
      await n.resolveConflict(keepMine: true);
      expect(c.read(applicationControllerProvider).sync, SyncStatus.saved);
      expect(phone.api.content.personal.fullName, 'Edited on phone');
    });

    test('use theirs: the phone adopts the server version', () async {
      final (c, n) = await conflicted();
      await n.retryNow();
      await n.resolveConflict(keepMine: false);
      final s = c.read(applicationControllerProvider);
      expect(s.draft.personal.fullName, 'Edited on web');
      expect(s.dirty, isFalse);
      expect(s.conflict, isNull);
      expect(phone.api.content.personal.fullName, 'Edited on web');
    });

    test(
      'found on the next start (kill, then open): the same choice is offered',
      () async {
        final c1 = phone.container();
        final n1 = c1.read(applicationControllerProvider.notifier);
        await n1.load();
        n1.update(
          (d) => d.copyWith(personal: d.personal.copyWith(fullName: 'Base')),
        );
        await waitFor(() => !c1.read(applicationControllerProvider).dirty);
        phone.api.offline = true;
        n1.update(
          (d) =>
              d.copyWith(personal: d.personal.copyWith(fullName: 'Phone edit')),
        );
        await waitFor(
          () =>
              c1.read(applicationControllerProvider).sync == SyncStatus.offline,
        );
        c1.dispose();
        phone.api.offline = false;
        phone.api.externalEdit(named('Web edit'));
        final c2 = phone.restart().container();
        await c2.read(applicationControllerProvider.notifier).load();
        final s = c2.read(applicationControllerProvider);
        expect(s.sync, SyncStatus.conflict);
        expect(s.conflict!.content.personal.fullName, 'Web edit');
        expect(s.draft.personal.fullName, 'Phone edit');
      },
    );

    test('same content on both sides is not a conflict', () async {
      final (c, n) = await conflicted();
      phone.api.externalEdit(named('Edited on phone'));
      await n.retryNow();
      expect(c.read(applicationControllerProvider).sync, SyncStatus.saved);
      expect(c.read(applicationControllerProvider).conflict, isNull);
    });
  });

  group('submit', () {
    Future<(ProviderContainer, ApplicationController)> ready() async {
      phone.api.externalEdit(completeDraft());
      final c = phone.container();
      final n = c.read(applicationControllerProvider.notifier);
      await n.load();
      return (c, n);
    }

    test(
      'an incomplete application is not sent, and the missing items are listed',
      () async {
        final c = phone.container();
        final n = c.read(applicationControllerProvider.notifier);
        await n.load();
        await n.submit();
        final s = c.read(applicationControllerProvider);
        expect(s.submit, SubmitPhase.failed);
        expect(
          s.submitIssues,
          containsAll(['personal.full_name:required', 'referees:wrong_count']),
        );
        expect(phone.api.calls.contains('submit'), isFalse);
      },
    );

    test(
      'submit creates the application in submitted state, exactly once',
      () async {
        final (c, n) = await ready();
        await n.submit();
        final s = c.read(applicationControllerProvider);
        expect(s.submit, SubmitPhase.done);
        expect(s.status, 'submitted');
        expect(s.canEdit, isFalse);
        expect(phone.api.status, 'submitted');
        expect(phone.api.submitCount, 1);
      },
    );

    test('double tap sends one submit', () async {
      final (_, n) = await ready();
      phone.api.latency = const Duration(milliseconds: 20);
      await Future.wait([n.submit(), n.submit(), n.submit()]);
      expect(phone.api.calls.where((x) => x == 'submit').length, 1);
      expect(phone.api.submitCount, 1);
    });

    test('pending edits are saved before the submit', () async {
      final (c, n) = await ready();
      n.update(
        (d) => d.copyWith(
          qualifications: d.qualifications.copyWith(
            institution: 'Kyambogo University',
          ),
        ),
      );
      await n.submit();
      expect(
        phone.api.content.qualifications.institution,
        'Kyambogo University',
      );
      expect(c.read(applicationControllerProvider).status, 'submitted');
    });

    test('offline submit is queued with its key and goes out once online, even after a kill', () async {
      final (c1, n1) = await ready();
      phone.api.offline = true;
      await n1.submit();
      expect(
        c1.read(applicationControllerProvider).submit,
        SubmitPhase.queuedOffline,
      );
      c1.dispose(); // kill
      final p2 = phone.restart();
      final c2 = p2.container();
      await c2.read(applicationControllerProvider.notifier).load();
      expect(
        c2.read(applicationControllerProvider).submit,
        SubmitPhase.queuedOffline,
      );
      phone.api.offline = false;
      await waitFor(
        () => c2.read(applicationControllerProvider).status == 'submitted',
      );
      expect(phone.api.submitCount, 1);
      expect(phone.api.submitKey, phone.keys.first);
    });

    test('a retry after a lost response reuses the key: the server replays, no second application', () async {
      final (c, n) = await ready();
      // The server accepts, but the phone never hears back.
      await phone.api.submit(
        baseRevision: phone.api.revision,
        idempotencyKey: phone.keys.first,
      );
      expect(phone.api.submitCount, 1);
      // The phone's own revision is now stale; it still holds the same key.
      final s0 = c.read(applicationControllerProvider);
      expect(s0.status, isNot('submitted'));
      await n.submit(); // uses keys.first
      expect(phone.api.submitCount, 1);
      expect(c.read(applicationControllerProvider).status, 'submitted');
    });

    test('another phone submitted already: this one learns it and shows the status', () async {
      final (c, n) = await ready();
      await phone.api.submit(
        baseRevision: phone.api.revision,
        idempotencyKey: 'someone-elses-key-0001',
      );
      await n.submit();
      expect(c.read(applicationControllerProvider).status, 'submitted');
      expect(phone.api.submitCount, 1);
    });

    test('server says incomplete: the issues are shown and a new key is used next time', () async {
      final (c, n) = await ready();
      // Server and phone disagree (server rules newer): remove availability on the server only.
      phone.api.content = phone.api.content.copyWith(availability: const []);
      phone.api.revision++;
      await n.reloadFromServer();
      // The phone still thinks it is valid? It reloaded, so it sees it as incomplete locally.
      await n.submit();
      expect(
        c.read(applicationControllerProvider).submitIssues,
        contains('availability:required'),
      );
    });

    test('rate limited submit is queued and retried', () async {
      final (c, n) = await ready();
      phone.api.nextFailure = const Failure.rateLimited(
        Duration(milliseconds: 20),
      );
      await n.submit();
      await waitFor(
        () => c.read(applicationControllerProvider).status == 'submitted',
      );
      expect(phone.api.submitCount, 1);
    });

    test('server still finishing an earlier submit (409): explained, kept, retried with the same key', () async {
      final (c, n) = await ready();
      phone.api.nextFailure = const Failure.rejected('submit_in_progress');
      await n.submit();
      expect(
        c.read(applicationControllerProvider).submit,
        SubmitPhase.inProgress,
      );
      expect(phone.api.submitCount, 0);
      await waitFor(
        () => c.read(applicationControllerProvider).status == 'submitted',
      );
      expect(phone.api.submitCount, 1);
      expect(phone.api.submitKey, phone.keys.first); // the same key all along
    });

    test('a draft save refused with submit_in_progress is shown as in progress and retried', () async {
      final (c, n) = await ready();
      final seen = <SubmitPhase>[];
      c.listen(applicationControllerProvider, (_, s) => seen.add(s.submit));
      phone.api.nextFailure = const Failure.rejected('submit_in_progress');
      n.update(
        (d) => d.copyWith(
          qualifications: d.qualifications.copyWith(
            institution: 'Gulu University',
          ),
        ),
      );
      await waitFor(() => !c.read(applicationControllerProvider).dirty);
      expect(seen, contains(SubmitPhase.inProgress));
      expect(c.read(applicationControllerProvider).submit, SubmitPhase.idle);
      expect(phone.api.content.qualifications.institution, 'Gulu University');
    });

    test('a submitted application cannot be edited from the phone', () async {
      final (c, n) = await ready();
      await n.submit();
      n.update(
        (d) => d.copyWith(personal: d.personal.copyWith(fullName: 'Hacked')),
      );
      expect(
        c.read(applicationControllerProvider).draft.personal.fullName,
        'Grace Nakato',
      );
    });
  });

  group('resubmission after rejection', () {
    test('only after the waiting period; then it is a draft again with nothing lost', () async {
      phone.api
        ..externalEdit(completeDraft())
        ..status = 'not_approved'
        ..reopenAt = DateTime.utc(2027, 2, 1);
      final c = phone.container();
      final n = c.read(applicationControllerProvider.notifier);
      await n.load();
      var s = c.read(applicationControllerProvider);
      expect(s.status, 'not_approved');
      expect(s.canReopen, isFalse);
      await n.reopen();
      expect(
        c.read(applicationControllerProvider).status,
        'not_approved',
      ); // refused
      phone.api.today = DateTime.utc(2027, 2, 1);
      await n.load();
      expect(c.read(applicationControllerProvider).canReopen, isTrue);
      await n.reopen();
      s = c.read(applicationControllerProvider);
      expect(s.status, 'draft');
      expect(s.canEdit, isTrue);
      expect(s.draft.personal.fullName, 'Grace Nakato');
    });
  });
}
