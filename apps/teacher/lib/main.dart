import 'package:appwrite/appwrite.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_school_shared/home_school_shared.dart';

import 'app.dart';

// Run with e.g.
// flutter run --dart-define=HS_ENV=staging \
//   --dart-define=HS_APPWRITE_ENDPOINT=https://<host>/v1 \
//   --dart-define=HS_APPWRITE_PROJECT=<project-id>
// Only public, non-secret values are ever passed.
Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final config = AppConfig.fromEnvironment();
  final client = Client()
    ..setEndpoint(config.endpoint)
    ..setProject(config.projectId);
  final db = await openAppDatabase();
  runApp(
    ProviderScope(
      overrides: [
        appConfigProvider.overrideWithValue(config),
        authServiceProvider.overrideWithValue(AppwriteAuthService(client)),
        offlineQueueProvider.overrideWithValue(OfflineQueue(db)),
      ],
      child: const TeacherApp(),
    ),
  );
}
