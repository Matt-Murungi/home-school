import 'package:go_router/go_router.dart';

import 'ui/application_screen.dart';

/// Routes of the application flow (outside the tab shell).
List<RouteBase> applicationRoutes() => [
  GoRoute(
    path: applyPath,
    builder: (context, state) => const ApplicationScreen(),
  ),
];
