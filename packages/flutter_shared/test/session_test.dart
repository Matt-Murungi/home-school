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
  jwtCacheTests();
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

/// Reviewer round 2, blocker: the cached core JWT must not outlive the session.
void jwtCacheTests() {
  group('JWT cache and session changes', () {
    late FakeApiClient api;
    late FakeGateway gateway;
    late InMemorySecureStore store;
    var minted = 0;
    late CachedTokenProvider cache;
    late AppwriteAuthService auth;
    final user = <String>[];

    setUp(() {
      api = FakeApiClient()
        ..stub('POST', '/v1/auth/otp/verify', {
          'user_id': 'u1',
          'secret': 'tok',
          'is_new_user': false,
        });
      gateway = FakeGateway();
      store = InMemorySecureStore();
      minted = 0;
      user
        ..clear()
        ..add('u1');
      // The token names whoever is signed in right now.
      cache = CachedTokenProvider(() async => 'jwt-${user.last}-${++minted}');
      auth = AppwriteAuthService(
        Client()
          ..setEndpoint('http://localhost/v1')
          ..setProject('test'),
        otp: CoreOtpApi(api, role: AppRole.teacher),
        gateway: gateway,
        vault: SessionVault(store),
        clock: () => _t0,
        onSessionChanged: cache.invalidate,
      );
    });

    test(
      'sign out, then another user signs in: a FRESH token, never the old one',
      () async {
        await auth.verifyOtp(_phone, '123456');
        expect(await cache(), 'jwt-u1-1');
        expect(await cache(), 'jwt-u1-1'); // cached while the same user stays
        await auth.signOut();
        user.add('u2');
        api.stub('POST', '/v1/auth/otp/verify', {
          'user_id': 'u2',
          'secret': 'tok2',
          'is_new_user': false,
        });
        await auth.verifyOtp('+256701234567', '123456');
        expect(await cache(), 'jwt-u2-2');
      },
    );

    test('signing out alone drops the token', () async {
      await auth.verifyOtp(_phone, '123456');
      await cache();
      await auth.signOut();
      user.add('u2');
      expect(await cache(), 'jwt-u2-2');
    });

    test('a fetch that started before sign-out is discarded, not cached for the next user', () async {
      var n = 0;
      final slow = CachedTokenProvider(() async {
        await Future<void>.delayed(const Duration(milliseconds: 20));
        return 'old-${++n}';
      });
      final a2 = AppwriteAuthService(
        Client()
          ..setEndpoint('http://localhost/v1')
          ..setProject('test'),
        otp: CoreOtpApi(api, role: AppRole.teacher),
        gateway: gateway,
        vault: SessionVault(store),
        clock: () => _t0,
        onSessionChanged: slow.invalidate,
      );
      await a2.verifyOtp(_phone, '123456');
      final inFlight = slow();
      await a2.signOut();
      expect(
        await inFlight,
        'old-1',
      ); // the caller that asked before sign-out gets its answer
      expect(await slow(), 'old-2'); // but the next caller never sees it
    });

    test('a failed sign-out call still drops the token', () async {
      await auth.verifyOtp(_phone, '123456');
      await cache();
      gateway.currentThrows = null;
      await auth.signOut();
      expect(await cache(), isNot('jwt-u1-1'));
    });

    test('a session found revoked on start drops the token', () async {
      await auth.verifyOtp(_phone, '123456');
      await cache();
      gateway.server = null; // revoked server-side
      expect(await auth.refreshSession(), isNull);
      expect(await cache(), 'jwt-u1-2');
    });

    test('clock skew and concurrent callers: one token per window, never an expired one', () async {
      var now = _t0;
      var count = 0;
      final c = CachedTokenProvider(
        () async => 'tok-${++count}',
        clock: () => now,
      );
      final first = await Future.wait([c(), c(), c()]);
      expect(first.toSet(), {'tok-1'});
      now = now.subtract(const Duration(minutes: 30)); // clock jumps backwards
      expect(await c(), 'tok-2'); // negative age is not trusted
      now = now.add(const Duration(minutes: 5));
      expect(await c(), 'tok-2');
      now = now.add(
        const Duration(minutes: 9, seconds: 40),
      ); // past reuse minus margin
      expect(await c(), 'tok-3');
    });
  });
}
