import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

const _phone = '+256772123456';

CoreOtpApi api(FakeApiClient f, {AppRole role = AppRole.parent}) =>
    CoreOtpApi(f, role: role, platform: 'android', appVersion: '1.0.0');

void main() {
  test('request posts phone and app and parses the timings', () async {
    final f = FakeApiClient()
      ..stub('POST', '/v1/auth/otp/request', {
        'status': 'sent',
        'resend_after_s': 30,
        'expires_in_s': 300,
      });
    final r = await api(f, role: AppRole.teacher).request(_phone);
    expect(r.resendAfter, const Duration(seconds: 30));
    expect(r.expiresIn, const Duration(minutes: 5));
    expect(f.requests.single.body, {'phone': _phone, 'app': 'teacher'});
  });

  test('verify posts code and device info and returns the token', () async {
    final f = FakeApiClient()
      ..stub('POST', '/v1/auth/otp/verify', {
        'user_id': 'u1',
        'secret': 'tok',
        'expire': '2026-11-25T09:02:00+00:00',
        'is_new_user': true,
        'dormant': false,
      });
    final t = await api(f).verify(_phone, '123456');
    expect((t.userId, t.secret, t.isNewUser), ('u1', 'tok', true));
    final body = f.requests.single.body!;
    expect(body['code'], '123456');
    expect(body['app'], 'parent');
    expect(body['device'], {'platform': 'android', 'app_version': '1.0.0'});
  });

  test('a 200 without a token is an unknown failure', () async {
    final f = FakeApiClient()..stub('POST', '/v1/auth/otp/verify', {});
    expect(
      () => api(f).verify(_phone, '123456'),
      throwsA(
        isA<OtpException>().having((e) => e.failure, 'f', OtpFailure.unknown),
      ),
    );
  });

  Map<String, Object?> err(
    String code, [
    Map<String, Object?> extra = const {},
  ]) => {
    'error': {'code': code, 'message': 'x', 'request_id': 'r', ...extra},
  };

  final mapping = <(int, String, Map<String, Object?>, OtpFailure)>[
    (422, 'invalid_phone', {}, OtpFailure.invalidPhone),
    (400, 'invalid_code', {'attempts_left': 3}, OtpFailure.wrongCode),
    (400, 'code_expired', {}, OtpFailure.expired),
    (429, 'too_many_attempts', {}, OtpFailure.tooManyAttempts),
    (429, 'resend_too_soon', {'retry_after_s': 12}, OtpFailure.rateLimited),
    (429, 'rate_limited', {'retry_after_s': 900}, OtpFailure.rateLimited),
    (429, 'otp_locked', {'retry_after_s': 1800}, OtpFailure.locked),
    (503, 'sms_unavailable', {}, OtpFailure.smsUnavailable),
    (422, 'number_unreachable', {}, OtpFailure.numberUnreachable),
    (403, 'account_blocked', {}, OtpFailure.accountBlocked),
    (503, 'auth_unavailable', {}, OtpFailure.serverUnavailable),
    (503, 'service_unavailable', {}, OtpFailure.serverUnavailable),
    (429, 'something_new', {}, OtpFailure.rateLimited),
    (502, 'proxy', {}, OtpFailure.serverUnavailable),
    (418, 'teapot', {}, OtpFailure.unknown),
  ];
  for (final (status, code, extra, failure) in mapping) {
    test('error $code ($status) maps to $failure', () async {
      final f = FakeApiClient()
        ..stub('POST', '/v1/auth/otp/verify', err(code, extra), status: status);
      await expectLater(
        api(f).verify(_phone, '123456'),
        throwsA(
          isA<OtpException>().having((e) => e.failure, 'failure', failure),
        ),
      );
    });
  }

  test('attempts left and retry-after are carried', () async {
    final f = FakeApiClient()
      ..stub(
        'POST',
        '/v1/auth/otp/verify',
        err('invalid_code', {'attempts_left': 2}),
        status: 400,
      );
    try {
      await api(f).verify(_phone, '000000');
      fail('should throw');
    } on OtpException catch (e) {
      expect(e.attemptsLeft, 2);
    }
    final g = FakeApiClient()
      ..stub(
        'POST',
        '/v1/auth/otp/request',
        err('rate_limited', {'retry_after_s': 600}),
        status: 429,
      );
    try {
      await api(g).request(_phone);
      fail('should throw');
    } on OtpException catch (e) {
      expect(e.retryAfter, const Duration(minutes: 10));
    }
  });

  test('no network, timeouts and connection errors mean offline', () async {
    for (final e in <Exception>[
      const SocketException('no route'),
      TimeoutException('slow'),
      const HttpException('closed'),
    ]) {
      final failing = _Throwing(e);
      await expectLater(
        CoreOtpApi(failing, role: AppRole.parent).request(_phone),
        throwsA(
          isA<OtpException>().having((x) => x.failure, 'f', OtpFailure.offline),
        ),
      );
    }
  });

  test('transport-level ApiException maps by status', () async {
    final f = FakeApiClient()..stubError('POST', '/v1/auth/otp/request', 503);
    await expectLater(
      api(f).request(_phone),
      throwsA(
        isA<OtpException>().having(
          (x) => x.failure,
          'f',
          OtpFailure.serverUnavailable,
        ),
      ),
    );
  });
}

class _Throwing implements ApiClient {
  _Throwing(this.e);
  final Exception e;
  @override
  Future<ApiResponse> send(ApiRequest request) async => throw e;
}
