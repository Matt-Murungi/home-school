import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../l10n/gen/hs_strings.dart';
import '../widgets/app_shell.dart';
import '../widgets/otp_screens.dart';
import '../widgets/welcome_screen.dart';

class TabSpec {
  const TabSpec({required this.path, required this.icon, required this.label});
  final String path;
  final IconData icon;
  final String Function(HsStrings) label;
}

/// Per-app navigation description.
class AppSpec {
  const AppSpec({required this.tabs, required this.welcomeBody});
  final List<TabSpec> tabs;
  final String Function(HsStrings) welcomeBody;

  /// Design Brief section 8.
  static final parent = AppSpec(
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

const signInPhonePath = '/sign-in/phone';
const signInCodePath = '/sign-in/code';

GoRouter buildAppRouter(AppSpec spec, {String initialLocation = '/'}) {
  return GoRouter(
    initialLocation: initialLocation,
    routes: [
      GoRoute(
        path: '/',
        builder: (context, state) => WelcomeScreen(
          body: spec.welcomeBody(HsStrings.of(context)),
          onGetStarted: () => context.go(signInPhonePath),
          onSignIn: () => context.go(signInPhonePath),
        ),
      ),
      // A01: phone + code, shared by both apps (P-02/P-03, T-02).
      GoRoute(
        path: signInPhonePath,
        builder: (context, state) =>
            PhoneEntryScreen(onCodeSent: () => context.go(signInCodePath)),
      ),
      GoRoute(
        path: signInCodePath,
        builder: (context, state) => CodeEntryScreen(
          onSignedIn: () => context.go('/${spec.tabs.first.path}'),
          onChangeNumber: () => context.go(signInPhonePath),
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
                      PlaceholderTab(label: t.label(HsStrings.of(context))),
                ),
              ],
            ),
        ],
      ),
    ],
  );
}
