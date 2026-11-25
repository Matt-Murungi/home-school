import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../auth/auth_service.dart';
import '../auth/roles.dart';
import '../auth/session_controller.dart';
import '../l10n/gen/hs_strings.dart';
import '../widgets/access_screens.dart';
import '../widgets/app_shell.dart';
import '../widgets/otp_screens.dart';
import '../widgets/welcome_screen.dart';
import 'route_guard.dart';

export 'route_guard.dart';

class TabSpec {
  const TabSpec({required this.path, required this.icon, required this.label});
  final String path;
  final IconData icon;
  final String Function(HsStrings) label;
}

/// Per-app navigation description.
class AppSpec {
  const AppSpec({
    required this.id,
    required this.tabs,
    required this.welcomeBody,
  });

  /// Which roles this app admits (A03): parent admits parent/guardian,
  /// teacher admits teacher.
  final AppId id;
  final List<TabSpec> tabs;
  final String Function(HsStrings) welcomeBody;

  /// Design Brief section 8.
  static final parent = AppSpec(
    id: AppId.parent,
    welcomeBody: (s) => s.welcomeParentBody,
    tabs: [
      TabSpec(path: 'home', icon: Icons.home_outlined, label: (s) => s.tabHome),
      TabSpec(
        path: 'sessions',
        icon: Icons.event_outlined,
        label: (s) => s.tabSessions,
      ),
      TabSpec(
        path: 'progress',
        icon: Icons.insights_outlined,
        label: (s) => s.tabProgress,
      ),
      TabSpec(
        path: 'billing',
        icon: Icons.account_balance_wallet_outlined,
        label: (s) => s.tabBilling,
      ),
      TabSpec(
        path: 'account',
        icon: Icons.person_outline,
        label: (s) => s.tabAccount,
      ),
    ],
  );

  static final teacher = AppSpec(
    id: AppId.teacher,
    welcomeBody: (s) => s.welcomeTeacherBody,
    tabs: [
      TabSpec(
        path: 'today',
        icon: Icons.today_outlined,
        label: (s) => s.tabToday,
      ),
      TabSpec(
        path: 'schedule',
        icon: Icons.calendar_month_outlined,
        label: (s) => s.tabSchedule,
      ),
      TabSpec(
        path: 'earnings',
        icon: Icons.account_balance_wallet_outlined,
        label: (s) => s.tabEarnings,
      ),
      TabSpec(
        path: 'growth',
        icon: Icons.trending_up_outlined,
        label: (s) => s.tabGrowth,
      ),
      TabSpec(
        path: 'account',
        icon: Icons.person_outline,
        label: (s) => s.tabAccount,
      ),
    ],
  );
}

final _gates = Expando<SessionGate>('session gate');

/// The gate behind a router built by [buildAppRouter]; `HsApp` feeds it the
/// session. Null for a router that was not built here (no guarding).
SessionGate? sessionGateOf(GoRouter router) => _gates[router];

/// Builds the guarded router (A03). Every route except the welcome, sign-in,
/// splash and suspended screens is protected: a signed-out user is sent to the
/// phone screen (remembering where they were going), a signed-in user whose
/// role this app does not admit sees the "wrong app" screen, and nothing
/// protected is built while the session is still loading.
///
/// [landingPath] is the hook for status gating: it picks where an admitted
/// user lands (default: the first tab). V01 puts every teacher on `/apply`;
/// V03 owns gating that by application status and plugs in here.
GoRouter buildAppRouter(
  AppSpec spec, {
  String initialLocation = '/',
  String Function(AuthSession session)? landingPath,
  Widget Function(BuildContext context, TabSpec tab)? tabBuilder,
}) {
  final gate = SessionGate(spec.id);
  String home() {
    final s = gate.state.session;
    final first = '/${spec.tabs.first.path}';
    return s == null ? first : (landingPath?.call(s) ?? first);
  }

  final router = GoRouter(
    initialLocation: initialLocation,
    refreshListenable: gate,
    redirect: (context, state) => guardRedirect(
      app: spec.id,
      session: gate.state,
      location: state.uri,
      homePath: home(),
    ),
    routes: [
      GoRoute(
        path: startPath,
        builder: (context, state) => const StartScreen(),
      ),
      GoRoute(
        path: welcomePath,
        builder: (context, state) => WelcomeScreen(
          body: spec.welcomeBody(HsStrings.of(context)),
          onGetStarted: () => context.go(_withFrom(signInPhonePath, state)),
          onSignIn: () => context.go(_withFrom(signInPhonePath, state)),
        ),
      ),
      // A01: phone + code, shared by both apps (P-02/P-03, T-02).
      GoRoute(
        path: signInPhonePath,
        builder: (context, state) => PhoneEntryScreen(
          onCodeSent: () => context.go(_withFrom(signInCodePath, state)),
        ),
      ),
      GoRoute(
        path: signInCodePath,
        builder: (context, state) => Consumer(
          builder: (context, ref, _) => CodeEntryScreen(
            // The router's redirect then leads an admitted user to `from`
            // or home, a wrong-role user to the wrong-app screen.
            onSignedIn: () => ref.read(sessionProvider.notifier).refresh(),
            onChangeNumber: () => context.go(_withFrom(signInPhonePath, state)),
            onSuspended: () => context.go(suspendedPath),
          ),
        ),
      ),
      GoRoute(
        path: wrongAppPath,
        builder: (context, state) => Consumer(
          builder: (context, ref, _) => WrongAppScreen(
            thisApp: spec.id,
            decision:
                gate.state.decision(spec.id) ??
                const AppDecision(AppDecisionKind.noRole),
            onSignOut: () => _signOut(context, ref),
          ),
        ),
      ),
      GoRoute(
        path: suspendedPath,
        builder: (context, state) => Consumer(
          builder: (context, ref, _) => SuspendedScreen(
            signedIn: gate.state.isSignedIn,
            onSignOut: () => _signOut(context, ref),
            onBack: () => context.go(welcomePath),
          ),
        ),
      ),
      StatefulShellRoute.indexedStack(
        builder: (context, state, shell) {
          final s = HsStrings.of(context);
          return AppShell(
            shell: shell,
            tabs: [
              for (final t in spec.tabs) (label: t.label(s), icon: t.icon),
            ],
          );
        },
        branches: [
          for (final t in spec.tabs)
            StatefulShellBranch(
              routes: [
                GoRoute(
                  path: '/${t.path}',
                  builder: (context, state) =>
                      tabBuilder?.call(context, t) ??
                      PlaceholderTab(label: t.label(HsStrings.of(context))),
                ),
              ],
            ),
        ],
      ),
    ],
  );
  _gates[router] = gate;
  return router;
}

Future<void> _signOut(BuildContext context, WidgetRef ref) async {
  await ref.read(sessionProvider.notifier).signOut();
  if (context.mounted) context.go(welcomePath);
}

/// Keeps the remembered deep-link target (`from`) across the sign-in steps.
String _withFrom(String to, GoRouterState state) {
  final from = safeFrom(state.uri.queryParameters['from']);
  return from == null
      ? to
      : Uri(path: to, queryParameters: {'from': from}).toString();
}
