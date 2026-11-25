import 'package:flutter/foundation.dart';

import '../auth/roles.dart';
import '../auth/session_controller.dart';

const welcomePath = '/';
const startPath = '/start';
const signInPhonePath = '/sign-in/phone';
const signInCodePath = '/sign-in/code';
const wrongAppPath = '/wrong-app';
const suspendedPath = '/suspended';

/// Paths a signed-out user may open. Everything else is protected (deny by
/// default: a new route is guarded unless it is listed here).
const publicPaths = <String>{
  welcomePath,
  startPath,
  signInPhonePath,
  signInCodePath,
  suspendedPath,
};

const _authPaths = <String>{welcomePath, signInPhonePath, signInCodePath};

bool isProtectedPath(String path) => !publicPaths.contains(path);

/// A `from` deep-link target is only honoured when it is a plain absolute
/// in-app path that is itself protected (never an auth screen, never a URL).
String? safeFrom(String? from) {
  if (from == null || from.isEmpty || from.length > 200) return null;
  if (!from.startsWith('/') || from.startsWith('//')) return null;
  if (RegExp(r'[\\\u0000- \u007f]').hasMatch(from)) return null;
  final uri = Uri.tryParse(from);
  if (uri == null || uri.hasScheme || uri.hasAuthority) return null;
  if (!isProtectedPath(uri.path)) return null;
  if (uri.path == wrongAppPath) return null;
  return from;
}

/// Mutable holder the router reads. The app widget copies the Riverpod
/// session into it (the router is built before the provider scope exists).
class SessionGate extends ChangeNotifier {
  SessionGate(this.app);
  final AppId app;
  SessionState _state = const SessionState.loading();
  SessionState get state => _state;

  void update(SessionState next) {
    if (identical(next, _state)) return;
    _state = next;
    notifyListeners();
  }
}

/// The one redirect rule (A03). Pure: given who is asking and where they
/// want to go, returns where they actually go, or null to stay.
///
/// - loading: only the splash and public screens; a protected location is held
///   back (so no protected content flashes before the session is known) and
///   remembered in `from`.
/// - signed out: protected paths go to the phone screen, remembering `from`.
/// - signed in and admitted: auth screens lead home (or to `from`).
/// - signed in, wrong role: only [wrongAppPath]; suspended: only [suspendedPath].
String? guardRedirect({
  required AppId app,
  required SessionState session,
  required Uri location,
  required String homePath,
}) {
  final path = location.path;
  final from = safeFrom(location.queryParameters['from']);
  String withFrom(String to, String? f) =>
      f == null ? to : Uri(path: to, queryParameters: {'from': f}).toString();

  switch (session.phase) {
    case SessionPhase.loading:
      if (path == startPath) return null;
      // The welcome screen is held back too: a returning user with a stored
      // session would otherwise see it flash before landing home.
      if (path == welcomePath) return startPath;
      if (!isProtectedPath(path)) return null;
      return withFrom(startPath, location.toString());
    case SessionPhase.signedOut:
      // A deep link that was held at the splash goes straight to the phone
      // screen; a plain start shows the welcome screen.
      if (path == startPath) {
        return from == null ? welcomePath : withFrom(signInPhonePath, from);
      }
      // After signing out of an info screen: the welcome screen.
      if (path == wrongAppPath) return welcomePath;
      if (path == suspendedPath) return null;
      if (!isProtectedPath(path)) return null;
      return withFrom(signInPhonePath, safeFrom(location.toString()));
    case SessionPhase.signedIn:
      final d = session.decision(app)!;
      switch (d.kind) {
        case AppDecisionKind.admit:
          if (path == startPath || _authPaths.contains(path)) {
            return from ?? homePath;
          }
          if (path == wrongAppPath || path == suspendedPath) return homePath;
          return null;
        case AppDecisionKind.suspended:
          return path == suspendedPath ? null : suspendedPath;
        case AppDecisionKind.wrongApp:
        case AppDecisionKind.noRole:
          return path == wrongAppPath ? null : wrongAppPath;
      }
  }
}
