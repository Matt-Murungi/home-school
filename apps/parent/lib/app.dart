import 'package:flutter/material.dart';
import 'package:home_school_shared/home_school_shared.dart';

/// Parent app root. State lives in Riverpod providers (see shared package).
class ParentApp extends StatefulWidget {
  const ParentApp({super.key});

  @override
  State<ParentApp> createState() => _ParentAppState();
}

class _ParentAppState extends State<ParentApp> {
  final _router = buildAppRouter(AppSpec.parent);

  @override
  void dispose() {
    _router.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => HsApp(router: _router);
}
