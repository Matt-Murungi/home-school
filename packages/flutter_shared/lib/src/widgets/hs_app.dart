import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:go_router/go_router.dart';

import '../auth/session_controller.dart';
import '../l10n/gen/hs_strings.dart';
import '../nav/app_router.dart';
import '../theme/hs_theme.dart';
import 'session_refresher.dart';

/// MaterialApp.router wired with theme and localisation; used by both apps.
/// It also keeps the router's session gate in step with the Riverpod session
/// (A03): the stored session is read on start, so protected screens are only
/// built once the role is known.
class HsApp extends ConsumerStatefulWidget {
  const HsApp({super.key, required this.router, this.builder});
  final GoRouter router;

  /// Optional app-level wrapper (used by tests to force text scale).
  final TransitionBuilder? builder;

  @override
  ConsumerState<HsApp> createState() => _HsAppState();
}

class _HsAppState extends ConsumerState<HsApp> {
  @override
  void initState() {
    super.initState();
    _start();
  }

  @override
  void didUpdateWidget(HsApp old) {
    super.didUpdateWidget(old);
    if (old.router != widget.router) _start(); // a new router = a new start
  }

  void _start() {
    final gate = sessionGateOf(widget.router);
    if (gate == null) return;
    // A new router's gate starts in `loading`: bring it level with the provider
    // (a no-op on first start), then re-read the stored session.
    gate.update(ref.read(sessionProvider));
    Future.microtask(() {
      if (mounted) ref.read(sessionProvider.notifier).start(gate.app);
    });
  }

  @override
  Widget build(BuildContext context) {
    ref.listen<SessionState>(sessionProvider, (_, next) {
      sessionGateOf(widget.router)?.update(next);
    });
    return SessionRefresher(
      child: MaterialApp.router(
        onGenerateTitle: (c) => HsStrings.of(c).appName,
        theme: buildHsTheme(),
        routerConfig: widget.router,
        builder: widget.builder,
        localizationsDelegates: const [
          HsStrings.delegate,
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
        supportedLocales: HsStrings.supportedLocales,
      ),
    );
  }
}
