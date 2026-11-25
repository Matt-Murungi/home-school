import 'dart:io' show Platform;

import 'package:appwrite/appwrite.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_school_shared/home_school_shared.dart';

import 'app.dart';
import 'application/data/application_api.dart';
import 'application/data/draft_database.dart';
import 'application/data/draft_store.dart';
import 'application/state/application_controller.dart';

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
      role: AppRole.teacher,
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
  // Authenticated calls to the core service carry a short-lived Appwrite JWT.
  final account = Account(client);
  final coreApi = HttpApiClient(
    config.coreUrl,
    bearerToken: () async => (await account.createJWT()).jwt,
  );
  final applicationApi = HttpApplicationApi(coreApi);
  final draftDb = await openDraftDatabase();
  runApp(
    ProviderScope(
      overrides: [
        appConfigProvider.overrideWithValue(config),
        authServiceProvider.overrideWithValue(auth),
        offlineQueueProvider.overrideWithValue(OfflineQueue(db)),
        applicationApiProvider.overrideWithValue(applicationApi),
        referenceSourceProvider.overrideWithValue(applicationApi),
        draftStoreProvider.overrideWithValue(DraftStore(draftDb)),
      ],
      child: TeacherApp(initialLocation: session == null ? '/' : '/apply'),
    ),
  );
}
