import 'dart:async';
import 'dart:math';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_school_shared/home_school_shared.dart';

import '../data/application_api.dart';
import '../data/draft_store.dart';
import '../model/application_draft.dart';
import '../model/reference.dart';
import '../model/rules.dart';

// -- dependencies (apps override in main(), tests with fakes) ---------------
final applicationApiProvider = Provider<ApplicationApi>(
  (ref) =>
      throw UnimplementedError('applicationApiProvider must be overridden'),
);
final referenceSourceProvider = Provider<ReferenceSource>(
  (ref) =>
      throw UnimplementedError('referenceSourceProvider must be overridden'),
);
final draftStoreProvider = Provider<DraftStore>(
  (ref) => throw UnimplementedError('draftStoreProvider must be overridden'),
);

/// Resolves which teacher this device's draft belongs to (the signed-in
/// user). Defaults to the current auth session.
final applicationUserIdProvider = Provider<Future<String> Function()>(
  (ref) => () async {
    final session = await ref.read(authServiceProvider).currentSession();
    if (session == null) throw StateError('not signed in');
    return session.userId;
  },
);

/// Kampala calendar date for age checks; overridden in tests.
final applicationTodayProvider = Provider<DateTime Function()>(
  (ref) => () {
    final k = DateTime.now().toUtc().add(const Duration(hours: 3));
    return DateTime.utc(k.year, k.month, k.day);
  },
);

class ApplicationTimings {
  const ApplicationTimings({
    this.autosave = const Duration(milliseconds: 800),
    this.retry = const Duration(seconds: 30),
  });
  final Duration autosave;
  final Duration retry;
}

final applicationTimingsProvider = Provider<ApplicationTimings>(
  (ref) => const ApplicationTimings(),
);

final submitKeyFactoryProvider = Provider<String Function()>((ref) {
  final rnd = Random.secure();
  return () =>
      List.generate(32, (_) => rnd.nextInt(16).toRadixString(16)).join();
});

enum SyncStatus { saved, saving, offline, conflict, rateLimited, locked }

enum SubmitPhase { idle, submitting, queuedOffline, done, failed }

class ApplicationState {
  const ApplicationState({
    this.loading = true,
    this.draft = const ApplicationDraft(),
    this.step = 0,
    this.baseRevision = 0,
    this.status = 'none',
    this.canEdit = true,
    this.dirty = false,
    this.sync = SyncStatus.saved,
    this.reference,
    this.referenceFailed = false,
    this.conflict,
    this.submit = SubmitPhase.idle,
    this.submitIssues = const [],
    this.draftIssues = const [],
    this.attempt = 0,
    this.submittedAt,
    this.reopenAt,
    this.canReopen = false,
    this.loadFailed = false,
    this.visited = const {},
    this.generation = 0,
  });

  /// Bumps when the draft is replaced from outside the form (server copy,
  /// conflict choice) so text fields rebuild with the new values.
  final int generation;
  final bool loading;
  final bool loadFailed;
  final ApplicationDraft draft;
  final int step;
  final int baseRevision;
  final String status;
  final bool canEdit;
  final bool dirty;
  final SyncStatus sync;
  final ReferenceData? reference;
  final bool referenceFailed;
  final ServerApplication? conflict;
  final SubmitPhase submit;

  /// Issues from the last submit try (local or server), `path:code`.
  final List<String> submitIssues;
  final List<String> draftIssues;
  final int attempt;
  final DateTime? submittedAt;
  final DateTime? reopenAt;
  final bool canReopen;

  /// Steps the teacher has left at least once (for showing errors only there).
  final Set<int> visited;

  bool get submitted => const {
    'submitted',
    'under_review',
    'approved',
    'not_approved',
  }.contains(status);

  ApplicationState copyWith({
    bool? loading,
    bool? loadFailed,
    ApplicationDraft? draft,
    int? step,
    int? baseRevision,
    String? status,
    bool? canEdit,
    bool? dirty,
    SyncStatus? sync,
    ReferenceData? reference,
    bool? referenceFailed,
    ServerApplication? conflict,
    bool clearConflict = false,
    SubmitPhase? submit,
    List<String>? submitIssues,
    List<String>? draftIssues,
    int? attempt,
    DateTime? submittedAt,
    DateTime? reopenAt,
    bool? canReopen,
    Set<int>? visited,
    int? generation,
  }) => ApplicationState(
    loading: loading ?? this.loading,
    loadFailed: loadFailed ?? this.loadFailed,
    draft: draft ?? this.draft,
    step: step ?? this.step,
    baseRevision: baseRevision ?? this.baseRevision,
    status: status ?? this.status,
    canEdit: canEdit ?? this.canEdit,
    dirty: dirty ?? this.dirty,
    sync: sync ?? this.sync,
    reference: reference ?? this.reference,
    referenceFailed: referenceFailed ?? this.referenceFailed,
    conflict: clearConflict ? null : (conflict ?? this.conflict),
    submit: submit ?? this.submit,
    submitIssues: submitIssues ?? this.submitIssues,
    draftIssues: draftIssues ?? this.draftIssues,
    attempt: attempt ?? this.attempt,
    submittedAt: submittedAt ?? this.submittedAt,
    reopenAt: reopenAt ?? this.reopenAt,
    canReopen: canReopen ?? this.canReopen,
    visited: visited ?? this.visited,
    generation: generation ?? this.generation,
  );
}

/// Offline-first controller for the application wizard.
///
/// Every edit is written to the local Drift draft immediately (so killing the
/// app loses nothing) and pushed to the server after a short pause. When the
/// network is down the draft stays dirty and is retried; the server copy is
/// reconciled by revision, never overwritten blindly.
class ApplicationController extends Notifier<ApplicationState> {
  Timer? _autosave;
  Timer? _retry;
  bool _disposed = false;
  bool _syncing = false;
  int _version = 0; // bumps on every local edit; detects edits during a save
  String? _submitKey;

  late final ApplicationApi _api = ref.read(applicationApiProvider);
  late final DraftStore _store = ref.read(draftStoreProvider);
  String _user = '';
  late final ApplicationTimings _t = ref.read(applicationTimingsProvider);

  @override
  ApplicationState build() {
    ref.onDispose(() {
      _disposed = true;
      _autosave?.cancel();
      _retry?.cancel();
    });
    return const ApplicationState();
  }

  /// Rules need the catalogue. Before it has loaded (and none is cached) the
  /// phone trusts the ids already chosen; the server checks them for real.
  RuleContext get ruleContext => RuleContext(
    today: ref.read(applicationTodayProvider)(),
    reference: state.reference ?? _trustingReference(state.draft),
  );

  static ReferenceData _trustingReference(ApplicationDraft d) => ReferenceData(
    curricula: [
      for (final c in d.curricula)
        CurriculumOption(
          id: c.curriculumId,
          code: '',
          name: '',
          levels: [for (final l in c.levelIds) NamedId(l, '')],
          subjects: [for (final x in c.subjectIds) NamedId(x, '')],
        ),
    ],
    zones: [for (final z in d.zoneIds) ZoneOption(z, '')],
  );

  List<String> issues({required bool final_}) =>
      validateApplication(state.draft, ruleContext, final_: final_);

  // -- loading -----------------------------------------------------------------
  Future<void> load() async {
    state = state.copyWith(loading: true, loadFailed: false);
    _user = await ref.read(applicationUserIdProvider)();
    final local = await _store.load(_user);
    _submitKey = local?.submitKey;
    ServerApplication? server;
    try {
      server = await _api.fetch();
    } on ApplicationFailure {
      server = null;
    }
    unawaited(_loadReference());
    if (_disposed) return;
    if (server == null) {
      // Offline start: the local draft (if any) is the truth for now.
      state = state.copyWith(
        loading: false,
        draft: local?.draft ?? const ApplicationDraft(),
        generation: state.generation + 1,
        step: local?.step ?? 0,
        baseRevision: local?.baseRevision ?? 0,
        status: local?.status ?? 'none',
        dirty: local?.dirty ?? false,
        sync: local == null ? SyncStatus.saved : SyncStatus.offline,
        loadFailed: local == null,
      );
      if (_submitKey != null) {
        state = state.copyWith(submit: SubmitPhase.queuedOffline);
      }
      if (local != null && (local.dirty || _submitKey != null)) {
        _scheduleRetry();
      }
      return;
    }
    _adopt(server, local);
    if (_submitKey != null && !state.submitted) {
      state = state.copyWith(submit: SubmitPhase.queuedOffline);
    }
    if (state.dirty || _submitKey != null) unawaited(flush());
  }

  void _adopt(ServerApplication server, LocalDraft? local) {
    final base = (
      status: server.status,
      canEdit: server.canEdit,
      attempt: server.attempt,
      submittedAt: server.submittedAt,
      reopenAt: server.reopenAt,
      canReopen: server.canReopen,
    );
    var next = state.copyWith(
      loading: false,
      status: base.status,
      canEdit: base.canEdit,
      attempt: base.attempt,
      submittedAt: base.submittedAt,
      reopenAt: base.reopenAt,
      canReopen: base.canReopen,
      sync: SyncStatus.saved,
    );
    final mine = local?.dirty == true;
    if (local == null || (!mine && server.revision >= local.baseRevision)) {
      // Nothing unsynced on this phone: the server copy wins.
      next = next.copyWith(
        draft: _keepLocalPhoto(server.content, local?.draft),
        // the step is a per-phone bookmark: this phone's own wins
        step: local?.step ?? server.step,
        baseRevision: server.revision,
        dirty: false,
        generation: state.generation + 1,
      );
    } else if (server.revision == local.baseRevision) {
      next = next.copyWith(
        draft: local.draft,
        step: local.step,
        baseRevision: local.baseRevision,
        dirty: true,
      );
    } else if (_same(server.content, local.draft)) {
      next = next.copyWith(
        draft: local.draft,
        step: local.step,
        baseRevision: server.revision,
        dirty: false,
      );
    } else {
      // Edited here while offline AND changed elsewhere: the teacher decides.
      next = next.copyWith(
        draft: local.draft,
        step: local.step,
        baseRevision: local.baseRevision,
        dirty: true,
        sync: SyncStatus.conflict,
        conflict: server,
      );
    }
    state = next;
    if (!_same(next.draft, local?.draft ?? const ApplicationDraft()) ||
        next.baseRevision != local?.baseRevision) {
      unawaited(_persist());
    }
  }

  static bool _same(ApplicationDraft a, ApplicationDraft b) =>
      a.toJson(forServer: true).toString() ==
      b.toJson(forServer: true).toString();

  /// The server never sees the local photo path; keep ours when its copy
  /// still describes the same photo.
  ApplicationDraft _keepLocalPhoto(
    ApplicationDraft server,
    ApplicationDraft? local,
  ) {
    final lp = local?.personal.photo;
    final sp = server.personal.photo;
    if (lp?.localPath == null || sp == null) return server;
    return server.copyWith(
      personal: server.personal.copyWith(
        photo: PhotoChoice(
          state: sp.state,
          localPath: lp!.localPath,
          fileId: sp.fileId,
          crop: sp.crop,
        ),
      ),
    );
  }

  /// The catalogue and zones: fetched when online and cached on the phone, so
  /// an offline start (after one online visit) still shows the choices.
  Future<void> _loadReference() async {
    try {
      final fresh = await ref.read(referenceSourceProvider).load();
      if (_disposed) return;
      state = state.copyWith(reference: fresh, referenceFailed: false);
      await _store.saveReference(fresh);
    } on ApplicationFailure {
      final cached = await _store.loadReference();
      if (_disposed) return;
      state = cached != null
          ? state.copyWith(reference: cached, referenceFailed: false)
          : state.copyWith(referenceFailed: true);
    }
  }

  Future<void> retryReference() => _loadReference();

  // -- editing -----------------------------------------------------------------
  void update(ApplicationDraft Function(ApplicationDraft d) change) {
    if (!state.canEdit) return;
    _version++;
    state = state.copyWith(
      draft: change(state.draft),
      dirty: true,
      sync: state.sync == SyncStatus.offline
          ? SyncStatus.offline
          : SyncStatus.saving,
      draftIssues: const [],
    );
    unawaited(_persist());
    _autosave?.cancel();
    _autosave = Timer(_t.autosave, () => unawaited(flush()));
  }

  /// Moving between steps is progress too: it is saved with the draft so a
  /// new phone resumes at the same step.
  void goToStep(int step) {
    final visited = {...state.visited, state.step};
    state = state.copyWith(
      step: step,
      visited: visited,
      dirty: state.canEdit ? true : state.dirty,
    );
    _version++;
    unawaited(_persist());
    if (state.dirty) unawaited(flush());
  }

  Future<void> _writes = Future.value();

  /// Resolves when every local write issued so far has finished (tests, and
  /// callers that must not continue before the draft is on disk).
  Future<void> get settled => _writes;

  /// Writes are chained so they land in order, and the values are captured
  /// now, not when the write runs.
  Future<void> _persist() {
    final draft = state.draft;
    final step = state.step;
    final base = state.baseRevision;
    final dirty = state.dirty;
    final status = state.status;
    final key = _submitKey;
    return _writes = _writes.then(
      (_) => _store.save(
        _user,
        draft: draft,
        step: step,
        baseRevision: base,
        dirty: dirty,
        status: status,
        submitKey: key,
      ),
    );
  }

  // -- syncing -----------------------------------------------------------------
  Future<void> flush() async {
    _autosave?.cancel();
    if (_syncing || _disposed || state.loading) return;
    if (state.sync == SyncStatus.conflict || !state.canEdit) return;
    _syncing = true;
    try {
      if (state.dirty) {
        final sent = _version;
        final draft = state.draft;
        state = state.copyWith(sync: SyncStatus.saving);
        try {
          final res = await _api.saveDraft(
            baseRevision: state.baseRevision,
            step: state.step,
            draft: draft,
          );
          if (_disposed) return;
          final edited = _version != sent;
          state = state.copyWith(
            baseRevision: res.revision,
            dirty: edited,
            draftIssues: res.issues,
            sync: edited ? SyncStatus.saving : SyncStatus.saved,
            status: res.status,
          );
          await _persist();
          if (edited) _autosave = Timer(_t.autosave, () => unawaited(flush()));
        } on DraftConflict catch (c) {
          if (_same(c.server.content, state.draft)) {
            state = state.copyWith(
              baseRevision: c.server.revision,
              dirty: false,
              sync: SyncStatus.saved,
            );
            await _persist();
          } else {
            state = state.copyWith(
              sync: SyncStatus.conflict,
              conflict: c.server,
              submit: state.submit == SubmitPhase.submitting
                  ? SubmitPhase.idle
                  : state.submit,
            );
          }
          return;
        } on OfflineFailure {
          state = state.copyWith(sync: SyncStatus.offline);
          _scheduleRetry();
          return;
        } on RateLimited catch (r) {
          state = state.copyWith(sync: SyncStatus.rateLimited);
          _scheduleRetry(r.retryAfter);
          return;
        } on Rejected catch (r) {
          if (r.code == 'application_locked' ||
              r.code == 'submit_in_progress') {
            state = state.copyWith(sync: SyncStatus.locked);
            unawaited(reloadFromServer());
          } else {
            state = state.copyWith(sync: SyncStatus.offline);
            _scheduleRetry();
          }
          return;
        }
      }
      if (_submitKey != null && !state.dirty && state.status != 'submitted') {
        await _doSubmit();
      }
    } finally {
      _syncing = false;
    }
  }

  void _scheduleRetry([Duration? after]) {
    _retry?.cancel();
    _retry = Timer(after ?? _t.retry, () => unawaited(flush()));
  }

  /// "Try again now" button and app-resume hook.
  Future<void> retryNow() => flush();

  Future<void> reloadFromServer() async {
    try {
      final s = await _api.fetch();
      if (_disposed) return;
      _adopt(s, null);
      await _persist();
    } on ApplicationFailure {
      // stays as is
    }
  }

  // -- conflict ----------------------------------------------------------------
  Future<void> resolveConflict({required bool keepMine}) async {
    final server = state.conflict;
    if (server == null) return;
    if (keepMine) {
      state = state.copyWith(
        baseRevision: server.revision,
        dirty: true,
        sync: SyncStatus.saving,
        clearConflict: true,
      );
    } else {
      state = state.copyWith(
        draft: _keepLocalPhoto(server.content, state.draft),
        baseRevision: server.revision,
        step: server.step,
        dirty: false,
        sync: SyncStatus.saved,
        clearConflict: true,
        generation: state.generation + 1,
      );
    }
    await _persist();
    if (keepMine) await flush();
  }

  // -- submit ------------------------------------------------------------------
  /// Validates, saves any pending edits, then submits exactly once (the same
  /// idempotency key is reused until the server answers).
  Future<void> submit() async {
    if (state.submit == SubmitPhase.submitting) return; // double tap
    final local = issues(final_: true);
    if (local.isNotEmpty) {
      state = state.copyWith(
        submitIssues: local,
        submit: SubmitPhase.failed,
        visited: {for (var i = 0; i < WizardStep.values.length; i++) i},
      );
      return;
    }
    _submitKey ??= ref.read(submitKeyFactoryProvider)();
    state = state.copyWith(
      submit: SubmitPhase.submitting,
      submitIssues: const [],
    );
    await _persist();
    await flush();
    if (state.submit == SubmitPhase.submitting && !_syncing) {
      // flush could not finish the save (offline, rate limit): the submit is
      // queued with its key and runs when the draft has synced.
      state = state.copyWith(submit: SubmitPhase.queuedOffline);
    }
  }

  Future<void> _doSubmit() async {
    final key = _submitKey!;
    state = state.copyWith(submit: SubmitPhase.submitting);
    try {
      final r = await _api.submit(
        baseRevision: state.baseRevision,
        idempotencyKey: key,
      );
      if (_disposed) return;
      _submitKey = null;
      await _store.clearSubmitKey(_user);
      state = state.copyWith(
        status: r.status,
        canEdit: false,
        attempt: r.attempt,
        submittedAt: r.submittedAt,
        submit: SubmitPhase.done,
        sync: SyncStatus.saved,
      );
      await _persist();
    } on OfflineFailure {
      state = state.copyWith(
        submit: SubmitPhase.queuedOffline,
        sync: SyncStatus.offline,
      );
      _scheduleRetry();
    } on RateLimited catch (r) {
      state = state.copyWith(
        submit: SubmitPhase.queuedOffline,
        sync: SyncStatus.rateLimited,
      );
      _scheduleRetry(r.retryAfter);
    } on Incomplete catch (e) {
      _submitKey = null;
      await _store.clearSubmitKey(_user);
      state = state.copyWith(
        submit: SubmitPhase.failed,
        submitIssues: e.issues,
      );
    } on DraftConflict catch (c) {
      state = state.copyWith(
        submit: SubmitPhase.idle,
        sync: SyncStatus.conflict,
        conflict: c.server,
      );
    } on Rejected catch (r) {
      _submitKey = null;
      await _store.clearSubmitKey(_user);
      state = state.copyWith(submit: SubmitPhase.failed);
      if (r.code == 'already_submitted' || r.code == 'application_locked') {
        await reloadFromServer();
      }
    }
  }

  Future<void> reopen() async {
    try {
      final s = await _api.reopen();
      _adopt(s, null);
      state = state.copyWith(submit: SubmitPhase.idle);
      await _persist();
    } on ApplicationFailure {
      // button stays; the status screen explains the wait
    }
  }
}

final applicationControllerProvider =
    NotifierProvider<ApplicationController, ApplicationState>(
      ApplicationController.new,
    );
