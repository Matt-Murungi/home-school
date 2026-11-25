import 'package:flutter/material.dart';
import 'package:home_school_shared/home_school_shared.dart';

/// Parent app root. State lives in Riverpod providers (see shared package).
class ParentApp extends StatefulWidget {
  const ParentApp({super.key, this.initialLocation = '/'});

  /// `/` (welcome) when signed out, the first tab when a session was restored.
  final String initialLocation;

  @override
  State<ParentApp> createState() => _ParentAppState();
}

class _ParentAppState extends State<ParentApp> {
  late final _router = buildAppRouter(
    AppSpec.parent,
    initialLocation: widget.initialLocation,
  );

  @override
  void dispose() {
    _router.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => HsApp(router: _router);
}
