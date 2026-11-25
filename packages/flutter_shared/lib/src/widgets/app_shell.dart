import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../l10n/gen/hs_strings.dart';
import '../theme/tokens.dart';

/// Bottom-tab scaffold (Design Brief section 8).
class AppShell extends StatelessWidget {
  const AppShell({super.key, required this.shell, required this.tabs});

  final StatefulNavigationShell shell;
  final List<({String label, IconData icon})> tabs;

  @override
  Widget build(BuildContext context) => Scaffold(
    body: shell,
    bottomNavigationBar: NavigationBar(
      selectedIndex: shell.currentIndex,
      onDestinationSelected: (i) =>
          shell.goBranch(i, initialLocation: i == shell.currentIndex),
      destinations: [
        for (final t in tabs)
          NavigationDestination(icon: Icon(t.icon), label: t.label),
      ],
    ),
  );
}

class PlaceholderTab extends StatelessWidget {
  const PlaceholderTab({super.key, required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SafeArea(
      child: ListView(
        padding: const EdgeInsets.all(HsSpace.lg),
        children: [
          Text(label, style: theme.textTheme.headlineMedium),
          const SizedBox(height: HsSpace.lg),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(HsSpace.lg),
              child: Text(
                HsStrings.of(context).placeholderTabBody(label),
                style: theme.textTheme.bodyLarge,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
