import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../api/api_client.dart';
import '../auth/auth_service.dart';
import '../config/app_config.dart';
import '../storage/offline_queue.dart';

final appConfigProvider = Provider<AppConfig>(
  (ref) => AppConfig.fromEnvironment(),
);

/// Apps override these in main(); tests override with fakes.
final apiClientProvider = Provider<ApiClient>(
  (ref) => throw UnimplementedError('apiClientProvider must be overridden'),
);
final authServiceProvider = Provider<AuthService>(
  (ref) => throw UnimplementedError('authServiceProvider must be overridden'),
);
final offlineQueueProvider = Provider<OfflineQueue>(
  (ref) => throw UnimplementedError('offlineQueueProvider must be overridden'),
);

final pendingActionsProvider = StreamProvider<int>(
  (ref) => ref.watch(offlineQueueProvider).watchCount(),
);
