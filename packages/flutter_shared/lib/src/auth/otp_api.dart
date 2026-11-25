import 'dart:async';
import 'dart:io';

import '../api/api_client.dart';
import 'otp_models.dart';

/// Calls the core service's `POST /v1/auth/otp/request` and `/verify`
/// (services/core `auth_otp`). Holds no secret: the SMS provider key and the
/// code hashing key exist only on the server.
class CoreOtpApi {
  CoreOtpApi(
    this._api, {
    required this.role,
    this.platform = 'unknown',
    this.appVersion = '',
  });

  final ApiClient _api;
  final AppRole role;
  final String platform;
  final String appVersion;

  Future<OtpRequestResult> request(String e164) async {
    final body = await _post('/v1/auth/otp/request', {
      'phone': e164,
      'app': role.name,
    });
    return OtpRequestResult(
      resendAfter: Duration(seconds: _int(body['resend_after_s'], 30)),
      expiresIn: Duration(seconds: _int(body['expires_in_s'], 300)),
    );
  }

  Future<OtpToken> verify(String e164, String code) async {
    final body = await _post('/v1/auth/otp/verify', {
      'phone': e164,
      'code': code,
      'app': role.name,
      'device': {
        'platform': platform,
        if (appVersion.isNotEmpty) 'app_version': appVersion,
      },
    });
    final userId = body['user_id'];
    final secret = body['secret'];
    if (userId is! String || secret is! String) {
      throw const OtpException(OtpFailure.unknown);
    }
    return OtpToken(
      userId: userId,
      secret: secret,
      isNewUser: body['is_new_user'] == true,
    );
  }

  Future<Map<String, Object?>> _post(
    String path,
    Map<String, Object?> body,
  ) async {
    final ApiResponse res;
    try {
      res = await _api.send(ApiRequest('POST', path, body: body));
    } on SocketException {
      throw const OtpException(OtpFailure.offline);
    } on TimeoutException {
      throw const OtpException(OtpFailure.offline);
    } on HttpException {
      throw const OtpException(OtpFailure.offline);
    } on ApiException catch (e) {
      throw _fromStatus(e.statusCode, const {});
    }
    if (res.statusCode == 200) return res.body;
    final err = res.body['error'];
    throw _fromStatus(res.statusCode, err is Map ? err : const {});
  }

  static int _int(Object? v, int fallback) => v is int ? v : fallback;

  static OtpException _fromStatus(int status, Map<Object?, Object?> err) {
    final code = err['code'];
    final left = err['attempts_left'];
    final retry = err['retry_after_s'];
    final retryAfter = retry is int ? Duration(seconds: retry) : null;
    return switch (code) {
      'invalid_phone' => const OtpException(OtpFailure.invalidPhone),
      'invalid_code' => OtpException(
        OtpFailure.wrongCode,
        attemptsLeft: left is int ? left : null,
      ),
      'code_expired' => const OtpException(OtpFailure.expired),
      'too_many_attempts' => const OtpException(OtpFailure.tooManyAttempts),
      'resend_too_soon' || 'rate_limited' => OtpException(
        OtpFailure.rateLimited,
        retryAfter: retryAfter,
      ),
      'otp_locked' => OtpException(OtpFailure.locked, retryAfter: retryAfter),
      'sms_unavailable' => const OtpException(OtpFailure.smsUnavailable),
      'number_unreachable' => const OtpException(OtpFailure.numberUnreachable),
      'account_blocked' => const OtpException(OtpFailure.accountBlocked),
      'service_unavailable' ||
      'auth_unavailable' => const OtpException(OtpFailure.serverUnavailable),
      _ when status == 429 => OtpException(
        OtpFailure.rateLimited,
        retryAfter: retryAfter,
      ),
      _ when status >= 500 => const OtpException(OtpFailure.serverUnavailable),
      _ => const OtpException(OtpFailure.unknown),
    };
  }
}
