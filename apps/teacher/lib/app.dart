import 'package:flutter/material.dart';
import 'package:home_school_shared/home_school_shared.dart';

import 'application/application_routes.dart';
import 'l10n/gen/teacher_strings.dart';

/// Teacher app root. State lives in Riverpod providers (see shared package).
class TeacherApp extends StatefulWidget {
  const TeacherApp({super.key, this.initialLocation = '/'});

  /// `/` (welcome) when signed out, the application flow when a session was
  /// restored.
  final String initialLocation;

  @override
  State<TeacherApp> createState() => _TeacherAppState();
}

class _TeacherAppState extends State<TeacherApp> {
  late final _router = buildAppRouter(
    AppSpec.teacher,
    initialLocation: widget.initialLocation,
    extraRoutes: applicationRoutes(),
  );

  @override
  void dispose() {
    _router.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) =>
      HsApp(router: _router, extraDelegates: const [TeacherStrings.delegate]);
}
