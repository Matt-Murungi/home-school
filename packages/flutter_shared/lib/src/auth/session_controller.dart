import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/providers.dart';
import 'auth_service.dart';
import 'roles.dart';

/// Where the app is in its life as seen by the router (A03).
enum SessionPhase {
  /// Reading the stored session. Protected screens are never built.
  loading,
  signedOut,
  signedIn,
}

class SessionState {
  const SessionState._(this.phase, this.session);
  const SessionState.loading() : this._(SessionPhase.loading, null);
  const SessionState.signedOut() : this._(SessionPhase.signedOut, null);
  const SessionState.signedIn(AuthSession session)
    : this._(SessionPhase.signedIn, session);

  final SessionPhase phase;
  final AuthSession? session;

  bool get isSignedIn => phase == SessionPhase.signedIn;

  /// What [app] shows this user; null unless signed in.
  AppDecision? decision(AppId app) {
    final s = session;
    if (s == null) return null;
    return decideApp(app, s.labels, s.status);
  }
}

/// Status that arrives from the core service (`GET /v1/me/access`), e.g. a
/// teacher suspended while signed in. The real adapter belongs to V03 (status
/// gating; it needs the bearer-token plumbing V01 adds); the default reports
/// nothing, so only what Appwrite says (labels, blocked) is used.
abstract interface class AccessSource {
  /// Null = unknown (offline, not wired): keep what the session says.
  Future<AccountStatus?> status(AppId app);
}

class NoAccessSource implements AccessSource {
  const NoAccessSource();
  @override
  Future<AccountStatus?> status(AppId app) async => null;
}

final accessSourceProvider = Provider<AccessSource>(
  (ref) => const NoAccessSource(),
);

/// Holds the session the router guards on. `refresh()` is called on start,
/// after sign-in and whenever the app returns to the foreground, so a role
/// change on the server re-evaluates routing without a new sign-in.
class SessionController extends Notifier<SessionState> {
  int _generation = 0;
  AppId? _app;

  @override
  SessionState build() => const SessionState.loading();

  /// Reads the stored session (offline: the cached one, with its cached
  /// roles) and publishes it.
  Future<void> start(AppId app) {
    _app = app;
    return refresh();
  }

  Future<void> refresh() async {
    final gen = ++_generation;
    AuthSession? s;
    try {
      s = await ref.read(authServiceProvider).refreshSession();
    } on Object {
      // No service wired (tests) or a transient failure: keep what we know
      // when signed in, otherwise treat as signed out.
      if (state.isSignedIn) return;
      s = null;
    }
    if (gen != _generation) return; // a newer refresh or a sign-out won
    if (s == null) {
      state = const SessionState.signedOut();
      return;
    }
    s = await _withServerStatus(s);
    if (gen != _generation) return;
    state = SessionState.signedIn(s);
  }

  Future<AuthSession> _withServerStatus(AuthSession s) async {
    try {
      final app = _app;
      if (app == null) return s;
      final st = await ref.read(accessSourceProvider).status(app);
      return st == null ? s : s.copyWith(status: st);
    } on Object {
      return s;
    }
  }

  Future<void> signOut() async {
    _generation++;
    try {
      await ref.read(authServiceProvider).signOut();
    } on Object {
      // Signed out locally either way; the server session expires on its own.
    }
    state = const SessionState.signedOut();
  }
}

final sessionProvider = NotifierProvider<SessionController, SessionState>(
  SessionController.new,
);
