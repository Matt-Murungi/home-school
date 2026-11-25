import 'package:flutter/material.dart';

import '../auth/roles.dart';
import '../l10n/gen/hs_strings.dart';
import '../theme/tokens.dart';

/// Shown while the stored session is being read. Protected content is never
/// built behind it.
class StartScreen extends StatelessWidget {
  const StartScreen({super.key});

  @override
  Widget build(BuildContext context) => Scaffold(
    body: Center(
      child: Semantics(
        label: HsStrings.of(context).startLoading,
        liveRegion: true,
        child: const CircularProgressIndicator(),
      ),
    ),
  );
}

/// A signed-in user whose role this app does not admit (A03): says which app
/// to use and offers sign out. Never a login error.
class WrongAppScreen extends StatelessWidget {
  const WrongAppScreen({
    super.key,
    required this.thisApp,
    required this.decision,
    required this.onSignOut,
  });

  final AppId thisApp;
  final AppDecision decision;
  final VoidCallback onSignOut;

  @override
  Widget build(BuildContext context) {
    final s = HsStrings.of(context);
    final suggested = decision.suggestedApp;
    final noAccess =
        decision.kind == AppDecisionKind.noRole || suggested == null;
    final title = noAccess
        ? s.noAccessTitle
        : thisApp == AppId.teacher
        ? s.wrongAppTitleTeacher
        : s.wrongAppTitleParent;
    final body = noAccess
        ? s.noAccessBody
        : switch (suggested) {
            AppId.parent => s.wrongAppToParent,
            AppId.teacher => s.wrongAppToTeacher,
            AppId.admin => s.wrongAppToAdmin,
          };
    return _InfoScreen(
      icon: Icons.swap_horiz,
      title: title,
      body: body,
      primaryLabel: s.signOut,
      onPrimary: onSignOut,
    );
  }
}

/// A suspended account (for the teacher app: a suspended teacher). A state to
/// explain, with sign out or back; not an error.
class SuspendedScreen extends StatelessWidget {
  const SuspendedScreen({
    super.key,
    required this.signedIn,
    required this.onSignOut,
    required this.onBack,
  });

  final bool signedIn;
  final VoidCallback onSignOut;
  final VoidCallback onBack;

  @override
  Widget build(BuildContext context) {
    final s = HsStrings.of(context);
    return _InfoScreen(
      icon: Icons.pause_circle_outline,
      title: s.suspendedTitle,
      body: s.suspendedBody,
      primaryLabel: signedIn ? s.signOut : s.suspendedBack,
      onPrimary: signedIn ? onSignOut : onBack,
    );
  }
}

class _InfoScreen extends StatelessWidget {
  const _InfoScreen({
    required this.icon,
    required this.title,
    required this.body,
    required this.primaryLabel,
    required this.onPrimary,
  });

  final IconData icon;
  final String title;
  final String body;
  final String primaryLabel;
  final VoidCallback onPrimary;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(HsSpace.xl),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SizedBox(height: HsSpace.xl),
              ExcludeSemantics(
                child: Icon(icon, size: 64, color: HsColors.primary),
              ),
              const SizedBox(height: HsSpace.lg),
              Semantics(
                header: true,
                liveRegion: true,
                child: Text(title, style: theme.textTheme.headlineMedium),
              ),
              const SizedBox(height: HsSpace.md),
              Text(body, style: theme.textTheme.bodyLarge),
              const SizedBox(height: HsSpace.xl),
              SizedBox(
                height: 48,
                child: FilledButton(
                  onPressed: onPrimary,
                  child: Text(primaryLabel),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
