import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:home_school_shared/home_school_shared.dart';
import 'package:home_school_teacher/application/application_routes.dart';
import 'package:home_school_teacher/application/ui/application_screen.dart';

void main() {
  test('new teachers land in the application flow after signing in', () {
    expect(AppSpec.teacher.postSignInPath, applyPath);
    expect(
      AppSpec.parent.postSignInPath,
      isNull,
    ); // the parent app is unchanged
  });

  test('the application route is registered outside the tab shell', () {
    final routes = applicationRoutes();
    expect(routes.whereType<GoRoute>().map((r) => r.path), [applyPath]);
    final router = buildAppRouter(AppSpec.teacher, extraRoutes: routes);
    addTearDown(router.dispose);
    expect(
      router.configuration.routes.whereType<GoRoute>().map((r) => r.path),
      contains(applyPath),
    );
  });
}
