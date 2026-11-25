/// Deployment environment, selected at build time with
/// `--dart-define=HS_ENV=dev|staging|prod`.
enum AppEnv { dev, staging, prod }

/// Non-secret runtime configuration. Never put API keys or secrets here: the
/// apps only ever hold the public Appwrite endpoint and project id.
class AppConfig {
  const AppConfig({
    required this.env,
    required this.endpoint,
    required this.projectId,
  });

  final AppEnv env;
  final String endpoint;
  final String projectId;

  static const _env = String.fromEnvironment('HS_ENV');
  static const _endpoint = String.fromEnvironment('HS_APPWRITE_ENDPOINT');
  static const _project = String.fromEnvironment('HS_APPWRITE_PROJECT');

  /// Reads `--dart-define` values (defaults to dev when absent).
  factory AppConfig.fromEnvironment() =>
      AppConfig.parse(env: _env, endpoint: _endpoint, projectId: _project);

  factory AppConfig.parse({
    required String env,
    required String endpoint,
    required String projectId,
  }) {
    final parsed = switch (env) {
      '' || 'dev' => AppEnv.dev,
      'staging' => AppEnv.staging,
      'prod' => AppEnv.prod,
      _ => throw FormatException(
        'Unknown HS_ENV "$env" (use dev, staging or prod)',
      ),
    };
    if (parsed != AppEnv.dev) {
      if (!endpoint.startsWith('https://')) {
        throw FormatException(
          'HS_APPWRITE_ENDPOINT must be https for ${parsed.name}',
        );
      }
      if (projectId.isEmpty) {
        throw FormatException(
          'HS_APPWRITE_PROJECT is required for ${parsed.name}',
        );
      }
    }
    return AppConfig(env: parsed, endpoint: endpoint, projectId: projectId);
  }

  @override
  String toString() => 'AppConfig(${env.name}, $endpoint, project=$projectId)';
}
