import 'package:appwrite/appwrite.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

void main() {
  group('FakeApiClient', () {
    test('returns canned responses and records requests', () async {
      final api = FakeApiClient()..stub('GET', '/me', {'name': 'Ada'});
      final r = await api.send(const ApiRequest('GET', '/me'));
      expect(r.body['name'], 'Ada');
      expect(api.requests.single.path, '/me');
    });

    test('unstubbed route throws ApiException (404)', () async {
      final api = FakeApiClient();
      expect(
        () => api.send(const ApiRequest('GET', '/nope')),
        throwsA(isA<ApiException>().having((e) => e.statusCode, 'status', 404)),
      );
    });

    test('stubbed failure surfaces as ApiException', () async {
      final api = FakeApiClient()..stubError('POST', '/pay', 500);
      expect(
        () => api.send(const ApiRequest('POST', '/pay')),
        throwsA(isA<ApiException>().having((e) => e.statusCode, 'status', 500)),
      );
    });
  });

  group('FakeAuthService', () {
    test('sign in, session stream and sign out', () async {
      final auth = FakeAuthService(users: {'a@x.ug': 'pw'});
      expect(await auth.currentSession(), isNull);
      final s = await auth.signIn('a@x.ug', 'pw');
      expect(s.email, 'a@x.ug');
      expect(await auth.currentSession(), isNotNull);
      await auth.signOut();
      expect(await auth.currentSession(), isNull);
    });

    test('wrong password throws AuthException', () async {
      final auth = FakeAuthService(users: {'a@x.ug': 'pw'});
      expect(() => auth.signIn('a@x.ug', 'bad'), throwsA(isA<AuthException>()));
    });
  });

  test('Appwrite adapter implements AuthService (no network used)', () {
    final client = Client()
      ..setEndpoint('http://localhost/v1')
      ..setProject('test');
    final AuthService auth = AppwriteAuthService(client);
    expect(auth, isA<AuthService>());
  });

  test('models placeholder', () {
    const p = UserProfile(id: '1', name: 'Ada', role: UserRole.parent);
    expect(p.role.name, 'parent');
    expect(UserRole.values, containsAll([UserRole.parent, UserRole.teacher]));
  });
}
