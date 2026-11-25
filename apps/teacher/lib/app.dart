import 'package:flutter/material.dart';
import 'package:home_school_shared/home_school_shared.dart';

/// Teacher app root. State lives in Riverpod providers (see shared package).
class TeacherApp extends StatefulWidget {
  const TeacherApp({super.key});

  @override
  State<TeacherApp> createState() => _TeacherAppState();
}

class _TeacherAppState extends State<TeacherApp> {
  final _router = buildAppRouter(AppSpec.teacher);

  @override
  void dispose() {
    _router.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => HsApp(router: _router);
}
