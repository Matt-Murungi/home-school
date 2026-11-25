import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

void main() {
  test('parses dev, staging and prod', () {
    expect(
      AppConfig.parse(
        env: 'dev',
        endpoint: 'http://localhost/v1',
        projectId: 'p',
      ).env,
      AppEnv.dev,
    );
    expect(
      AppConfig.parse(
        env: 'staging',
        endpoint: 'https://s.example/v1',
        projectId: 'p',
        coreUrl: 'https://core.example',
      ).env,
      AppEnv.staging,
    );
    expect(
      AppConfig.parse(
        env: 'prod',
        endpoint: 'https://p.example/v1',
        projectId: 'p',
        coreUrl: 'https://core.example',
      ).env,
      AppEnv.prod,
    );
  });

  test('empty env defaults to dev', () {
    expect(
      AppConfig.parse(env: '', endpoint: '', projectId: '').env,
      AppEnv.dev,
    );
  });

  test('unknown env is rejected', () {
    expect(
      () => AppConfig.parse(env: 'qa', endpoint: '', projectId: ''),
      throwsFormatException,
    );
  });

  test('staging and prod require https endpoint and project id', () {
    expect(
      () =>
          AppConfig.parse(env: 'prod', endpoint: 'http://x/v1', projectId: 'p'),
      throwsFormatException,
    );
    expect(
      () => AppConfig.parse(
        env: 'staging',
        endpoint: 'https://x/v1',
        projectId: '',
      ),
      throwsFormatException,
    );
  });

  test('core service URL: https outside dev, trailing slash trimmed (A01)', () {
    expect(
      AppConfig.parse(
        env: 'staging',
        endpoint: 'https://x/v1',
        projectId: 'p',
        coreUrl: 'https://core.example/',
      ).coreUrl,
      'https://core.example',
    );
    for (final bad in ['', 'http://core.example']) {
      expect(
        () => AppConfig.parse(
          env: 'prod',
          endpoint: 'https://x/v1',
          projectId: 'p',
          coreUrl: bad,
        ),
        throwsFormatException,
      );
    }
    expect(
      AppConfig.parse(
        env: 'dev',
        endpoint: '',
        projectId: '',
        coreUrl: 'http://10.0.2.2:8080',
      ).coreUrl,
      'http://10.0.2.2:8080',
    );
  });

  test('config carries no secret fields', () {
    final c = AppConfig.parse(
      env: 'dev',
      endpoint: 'http://localhost/v1',
      projectId: 'p',
    );
    expect(c.toString().toLowerCase(), isNot(contains('key')));
    expect(c.toString().toLowerCase(), isNot(contains('secret')));
  });

  test('fromEnvironment without defines yields dev', () {
    expect(AppConfig.fromEnvironment().env, AppEnv.dev);
  });
}
