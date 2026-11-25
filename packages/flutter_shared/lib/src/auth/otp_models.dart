/// Why a phone-code step failed. The UI maps each to its own message.
enum OtpFailure {
  invalidPhone,
  wrongCode,
  expired,
  tooManyAttempts,
  rateLimited,
  locked,
  offline,
  smsUnavailable,
  numberUnreachable,
  accountBlocked,

  /// A03: the account (for the teacher app: the teacher profile) is suspended.
  /// A state to explain, not a sign-in error.
  suspended,

  /// A03 (owner question 27): staff accounts sign in to the admin dashboard.
  staffAccount,

  /// A03: staff withdrew this role; signing in does not return it.
  roleRevoked,
  serverUnavailable,
  unknown,
}

class OtpException implements Exception {
  const OtpException(this.failure, {this.attemptsLeft, this.retryAfter});
  final OtpFailure failure;

  /// For [OtpFailure.wrongCode]: tries left on this code.
  final int? attemptsLeft;

  /// For [OtpFailure.rateLimited] / [OtpFailure.locked]: when to try again.
  final Duration? retryAfter;

  @override
  String toString() => 'OtpException($failure)';
}

class OtpRequestResult {
  const OtpRequestResult({required this.resendAfter, required this.expiresIn});
  final Duration resendAfter;
  final Duration expiresIn;
}

/// What the core service returns after a correct code: a one-time Appwrite
/// custom token the app exchanges for a session (`account.createSession`).
class OtpToken {
  const OtpToken({
    required this.userId,
    required this.secret,
    required this.isNewUser,
  });
  final String userId;
  final String secret;
  final bool isNewUser;
}

/// The two apps. Sent with the request; the server only lets it choose
/// between these two self-service roles.
enum AppRole { parent, teacher }
