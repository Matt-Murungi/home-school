import 'package:appwrite/appwrite.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

const _phone = '+256772123456';
final _t0 = DateTime.utc(2026, 11, 25, 9);

class FakeGateway implements SessionGateway {
  GatewaySession? server;
  int extended = 0;
  bool deleted = false;
  AppwriteException? createError;
  AppwriteException? currentError;
  String? usedSecret;
  Object? currentThrows;

  @override
  Future<GatewaySession> createSession(String userId, String secret) async {
    if (createError != null) throw createError!;
    return server = GatewaySession(
      userId: userId,
      id: 'sess1',
      expiresAt: _t0.add(const Duration(days: 30)),
      secret: 'session-secret',
    );
  }

  @override
  Future<GatewaySession?> current() async {
    if (currentThrows != null) throw currentThrows!;
    if (currentError != null) throw currentError!;
    return server;
  }

  @override
  Future<GatewaySession> extend(String sessionId) async {
    extended++;
    return server = GatewaySession(
      userId: server!.userId,
      id: sessionId,
      expiresAt: _t0.add(const Duration(days: 60)),
      secret: server!.secret,
    );
  }

  @override
  Future<void> deleteCurrent() async {
    deleted = true;
    server = null;
  }

  @override
  void useSessionSecret(String secret) => usedSecret = secret;
}

void main() {
  late FakeApiClient api;
  late FakeGateway gateway;
  late InMemorySecureStore store;
  var now = _t0;

  AppwriteAuthService build() => AppwriteAuthService(
    Client()
      ..setEndpoint('http://localhost/v1')
      ..setProject('test'),
    otp: CoreOtpApi(api, role: AppRole.parent),
    gateway: gateway,
    vault: SessionVault(store),
    clock: () => now,
  );

  setUp(() {
    api = FakeApiClient()
      ..stub('POST', '/v1/auth/otp/verify', {
        'user_id': 'u1',
        'secret': 'tok',
        'is_new_user': true,
      })
      ..stub('POST', '/v1/auth/otp/request', {
        'resend_after_s': 30,
        'expires_in_s': 300,
      });
    gateway = FakeGateway();
    store = InMemorySecureStore();
    now = _t0;
  });

  group('sign-in persists the session in secure storage', () {
    test('verify exchanges the token for a session and stores it', () async {
      final s = await build().verifyOtp(_phone, '123456');
      expect(s.userId, 'u1');
      expect(s.phoneMasked, '+256 *** *** 456');
      expect(s.isNewUser, isTrue);
      final raw = store.values['hs.session.v1']!;
      expect(raw, contains('sess1'));
      expect(
        raw,
        isNot(contains(_phone)),
        reason: 'full number is never stored',
      );
      expect(raw, isNot(contains('tok')), reason: 'one-time token is not kept');
    });

    test('an expired or used token asks for a new code', () async {
      gateway.createError = AppwriteException('bad', 401);
      await expectLater(
        build().verifyOtp(_phone, '123456'),
        throwsA(
          isA<OtpException>().having((e) => e.failure, 'f', OtpFailure.expired),
        ),
      );
      expect(store.values, isEmpty);
    });

    test('Appwrite down during the exchange is a retriable error', () async {
      gateway.createError = AppwriteException('boom', 503);
      await expectLater(
        build().verifyOtp(_phone, '123456'),
        throwsA(
          isA<OtpException>().having(
            (e) => e.failure,
            'f',
            OtpFailure.serverUnavailable,
          ),
        ),
      );
    });

    test('wrong code never reaches Appwrite or storage', () async {
      api.stub('POST', '/v1/auth/otp/verify', {
        'error': {'code': 'invalid_code', 'attempts_left': 4},
      }, status: 400);
      await expectLater(
        build().verifyOtp(_phone, '000000'),
        throwsA(isA<OtpException>()),
      );
      expect(gateway.server, isNull);
      expect(store.values, isEmpty);
    });

    test('requestOtp goes through the core api', () async {
      final r = await build().requestOtp(_phone);
      expect(r.resendAfter.inSeconds, 30);
    });
  });

  group('restore and refresh', () {
    test('no stored session -> signed out', () async {
      expect(await build().refreshSession(), isNull);
    });

    test(
      'valid session far from expiry is restored without extending',
      () async {
        final svc = build();
        await svc.verifyOtp(_phone, '123456');
        now = _t0.add(const Duration(days: 1));
        final s = await svc.refreshSession();
        expect(s!.userId, 'u1');
        expect(gateway.extended, 0);
        expect(gateway.usedSecret, 'session-secret');
      },
    );

    test(
      'a session within the refresh window is extended and re-stored',
      () async {
        final svc = build();
        await svc.verifyOtp(_phone, '123456');
        now = _t0.add(const Duration(days: 25)); // 5 days left < 7
        final s = await svc.refreshSession();
        expect(gateway.extended, 1);
        expect(s!.expiresAt, _t0.add(const Duration(days: 60)));
        expect(store.values['hs.session.v1'], contains('2027-01-24'));
      },
    );

    test('an expired stored session is cleared', () async {
      final svc = build();
      await svc.verifyOtp(_phone, '123456');
      now = _t0.add(const Duration(days: 31));
      expect(await svc.refreshSession(), isNull);
      expect(store.values, isEmpty);
    });

    test('a session revoked on the server is cleared', () async {
      final svc = build();
      await svc.verifyOtp(_phone, '123456');
      gateway.server = null;
      expect(await svc.refreshSession(), isNull);
      expect(store.values, isEmpty);
    });

    test('401 from Appwrite signs out', () async {
      final svc = build();
      await svc.verifyOtp(_phone, '123456');
      gateway.currentError = AppwriteException('no', 401);
      expect(await svc.refreshSession(), isNull);
      expect(store.values, isEmpty);
    });

    test(
      'offline at start keeps the user signed in with the stored session',
      () async {
        final svc = build();
        await svc.verifyOtp(_phone, '123456');
        gateway.currentThrows = Exception('SocketException');
        final s = await svc.refreshSession();
        expect(s!.userId, 'u1');
        expect(store.values, isNotEmpty);
      },
    );

    test('a server error keeps the stored session too', () async {
      final svc = build();
      await svc.verifyOtp(_phone, '123456');
      gateway.currentError = AppwriteException('boom', 503);
      expect((await svc.refreshSession())!.userId, 'u1');
    });

    test('sign out clears storage and the server session', () async {
      final svc = build();
      await svc.verifyOtp(_phone, '123456');
      await svc.signOut();
      expect(store.values, isEmpty);
      expect(gateway.deleted, isTrue);
    });

    test(
      'sign out when the server session is already gone still succeeds',
      () async {
        final svc = build();
        await svc.verifyOtp(_phone, '123456');
        gateway.server = null;
        await svc.signOut();
        expect(store.values, isEmpty);
      },
    );
  });

  group('SessionVault', () {
    test('round trip', () async {
      final v = SessionVault(store);
      await v.save(StoredSession(userId: 'u', sessionId: 's', expiresAt: _t0));
      expect((await v.load())!.userId, 'u');
    });
    test('corrupt data is treated as signed out and removed', () async {
      store.values['hs.session.v1'] = '{not json';
      expect(await SessionVault(store).load(), isNull);
      expect(store.values, isEmpty);
      store.values['hs.session.v1'] = '{"userId": 1}';
      expect(await SessionVault(store).load(), isNull);
      expect(store.values, isEmpty);
    });
  });

  test('the app uses secure storage, not shared preferences', () {
    expect(const FlutterSecureKeyValueStore(), isA<SecureKeyValueStore>());
  });
}
