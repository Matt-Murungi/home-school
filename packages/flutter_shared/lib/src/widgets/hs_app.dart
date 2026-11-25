import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:go_router/go_router.dart';

import '../l10n/gen/hs_strings.dart';
import '../theme/hs_theme.dart';
import 'session_refresher.dart';

/// MaterialApp.router wired with theme and localisation; used by both apps.
class HsApp extends StatelessWidget {
  const HsApp({
    super.key,
    required this.router,
    this.builder,
    this.extraDelegates = const [],
  });
  final GoRouter router;

  /// Optional app-level wrapper (used by tests to force text scale).
  final TransitionBuilder? builder;

  /// App-specific string tables (the teacher app adds its own).
  final List<LocalizationsDelegate<dynamic>> extraDelegates;

  @override
  Widget build(BuildContext context) => SessionRefresher(
    child: MaterialApp.router(
      onGenerateTitle: (c) => HsStrings.of(c).appName,
      theme: buildHsTheme(),
      routerConfig: router,
      builder: builder,
      localizationsDelegates: [
        HsStrings.delegate,
        ...extraDelegates,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      supportedLocales: HsStrings.supportedLocales,
    ),
  );
}
