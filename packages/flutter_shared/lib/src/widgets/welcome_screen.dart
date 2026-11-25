import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../l10n/gen/hs_strings.dart';
import '../providers/providers.dart';
import '../theme/tokens.dart';

/// Themed placeholder first screen shared by both apps.
class WelcomeScreen extends ConsumerWidget {
  const WelcomeScreen({
    super.key,
    required this.body,
    required this.onGetStarted,
    this.onSignIn,
  });

  final String body;
  final VoidCallback onGetStarted;

  /// P-01 "I already have an account" (same phone flow); hidden when null.
  final VoidCallback? onSignIn;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final s = HsStrings.of(context);
    final theme = Theme.of(context);
    final env = ref.watch(appConfigProvider).env;
    return Scaffold(
      body: SafeArea(
        child: LayoutBuilder(
          builder: (context, constraints) => SingleChildScrollView(
            padding: const EdgeInsets.all(HsSpace.xl),
            child: ConstrainedBox(
              constraints: BoxConstraints(
                minHeight: constraints.maxHeight - 2 * HsSpace.xl,
              ),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    s.appName,
                    style: theme.textTheme.titleMedium?.copyWith(
                      color: HsColors.primary,
                    ),
                  ),
                  const SizedBox(height: HsSpace.lg),
                  Text(s.welcomeTitle, style: theme.textTheme.headlineMedium),
                  const SizedBox(height: HsSpace.md),
                  Text(body, style: theme.textTheme.bodyLarge),
                  const SizedBox(height: HsSpace.xxl),
                  FilledButton(
                    onPressed: onGetStarted,
                    child: Text(s.getStarted),
                  ),
                  if (onSignIn != null) ...[
                    const SizedBox(height: HsSpace.sm),
                    TextButton(
                      key: const Key('welcome-sign-in'),
                      onPressed: onSignIn,
                      child: Text(s.signInExisting),
                    ),
                  ],
                  const SizedBox(height: HsSpace.lg),
                  Text(
                    s.environmentLabel(env.name),
                    style: theme.textTheme.bodySmall,
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
