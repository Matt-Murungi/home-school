import 'phone.dart';
import 'otp_models.dart';

class AuthSession {
  const AuthSession({
    required this.userId,
    this.email = '',
    this.phoneMasked,
    this.expiresAt,
    this.isNewUser = false,
  });
  final String userId;
  final String email;

  /// `+256 *** *** 456`; the full number is never kept in app state.
  final String? phoneMasked;
  final DateTime? expiresAt;
  final bool isNewUser;
}

class AuthException implements Exception {
  const AuthException(this.message);
  final String message;
  @override
  String toString() => 'AuthException($message)';
}

/// Auth contract the apps depend on. Real adapter: AppwriteAuthService.
abstract interface class AuthService {
  Future<AuthSession?> currentSession();
  Future<AuthSession> signIn(String email, String password);
  Future<void> signOut();

  /// Phone sign-in (A01): asks the core service to SMS a 6-digit code to
  /// [phoneE164] (`+2567...`). Throws [OtpException].
  Future<OtpRequestResult> requestOtp(String phoneE164);

  /// Checks the code with the core service, exchanges the returned custom
  /// token for a session and stores it securely. Throws [OtpException].
  Future<AuthSession> verifyOtp(String phoneE164, String code);

  /// On app start and resume: returns the stored session if it is still
  /// valid, extending it when close to expiry; null when signed out or the
  /// session is gone. Never throws for "signed out".
  Future<AuthSession?> refreshSession();
}

/// Fake used by every widget test. Scenario knobs cover each state of the
/// phone flow: wrong code (with an attempts budget), expired, too many
/// attempts, rate limited, offline, SMS outage.
class FakeAuthService implements AuthService {
  FakeAuthService({Map<String, String> users = const {}})
    : _users = users; // ignore: prefer_initializing_formals
  final Map<String, String> _users;
  AuthSession? _session;

  // -- phone flow scenario state -------------------------------------------
  /// The one code the fake accepts.
  String validCode = '123456';
  int maxAttempts = 5;
  int _attempts = 0;
  bool offline = false;
  bool codeExpired = false;

  /// Consumed one per call, before normal behaviour.
  final requestFailures = <OtpException>[];
  final verifyFailures = <OtpException>[];
  Duration resendAfter = const Duration(seconds: 30);
  Duration latency = Duration.zero;

  final requestedPhones = <String>[];
  final verifiedCodes = <String>[];
  int refreshCalls = 0;
  DateTime? sessionExpiresAt;

  @override
  Future<AuthSession?> currentSession() async => _session;

  @override
  Future<AuthSession> signIn(String email, String password) async {
    if (_users[email] != password || !_users.containsKey(email)) {
      throw const AuthException('Invalid credentials');
    }
    return _session = AuthSession(userId: 'fake-$email', email: email);
  }

  @override
  Future<void> signOut() async => _session = null;

  Future<void> _wait() async {
    if (latency > Duration.zero) await Future<void>.delayed(latency);
  }

  @override
  Future<OtpRequestResult> requestOtp(String phoneE164) async {
    await _wait();
    requestedPhones.add(phoneE164);
    if (offline) throw const OtpException(OtpFailure.offline);
    if (requestFailures.isNotEmpty) throw requestFailures.removeAt(0);
    _attempts = 0;
    codeExpired = false;
    return OtpRequestResult(
      resendAfter: resendAfter,
      expiresIn: const Duration(minutes: 5),
    );
  }

  @override
  Future<AuthSession> verifyOtp(String phoneE164, String code) async {
    await _wait();
    verifiedCodes.add(code);
    if (offline) throw const OtpException(OtpFailure.offline);
    if (verifyFailures.isNotEmpty) throw verifyFailures.removeAt(0);
    if (codeExpired) throw const OtpException(OtpFailure.expired);
    if (_attempts >= maxAttempts) {
      throw const OtpException(OtpFailure.tooManyAttempts);
    }
    _attempts++;
    if (code != validCode) {
      throw OtpException(
        OtpFailure.wrongCode,
        attemptsLeft: maxAttempts - _attempts,
      );
    }
    sessionExpiresAt = DateTime.utc(2027, 1, 1);
    return _session = AuthSession(
      userId: 'fake-phone-$phoneE164',
      phoneMasked: maskPhone(phoneE164),
      expiresAt: sessionExpiresAt,
      isNewUser: true,
    );
  }

  @override
  Future<AuthSession?> refreshSession() async {
    refreshCalls++;
    return _session;
  }
}
