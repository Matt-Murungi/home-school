import 'dart:io' show Platform;

import 'package:appwrite/appwrite.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_school_shared/home_school_shared.dart';

import 'app.dart';

// Run with e.g.
// flutter run --dart-define=HS_ENV=staging \
//   --dart-define=HS_APPWRITE_ENDPOINT=https://<host>/v1 \
//   --dart-define=HS_APPWRITE_PROJECT=<project-id> \
//   --dart-define=HS_CORE_URL=https://<core-service-host>
// Only public, non-secret values are ever passed.
Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final config = AppConfig.fromEnvironment();
  final client = Client()
    ..setEndpoint(config.endpoint)
    ..setProject(config.projectId);
  final auth = AppwriteAuthService(
    client,
    otp: CoreOtpApi(
      HttpApiClient(config.coreUrl),
      role: AppRole.parent,
      platform: Platform.operatingSystem,
    ),
  );
  // Restore (and extend if close to expiry) the securely stored session.
  AuthSession? session;
  try {
    session = await auth.refreshSession();
  } on Object {
    session = null;
  }
  final db = await openAppDatabase();
  runApp(
    ProviderScope(
      overrides: [
        appConfigProvider.overrideWithValue(config),
        authServiceProvider.overrideWithValue(auth),
        offlineQueueProvider.overrideWithValue(OfflineQueue(db)),
      ],
      child: ParentApp(initialLocation: session == null ? '/' : '/home'),
    ),
  );
}
