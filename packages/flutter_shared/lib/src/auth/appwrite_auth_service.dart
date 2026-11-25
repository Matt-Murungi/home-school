import 'package:appwrite/appwrite.dart';
import 'package:appwrite/models.dart' as models;

import 'auth_service.dart';
import 'otp_api.dart';
import 'otp_models.dart';
import 'phone.dart';
import 'roles.dart';
import 'session_store.dart';

/// The few Appwrite `Account` calls the adapter needs, so tests can fake them.
abstract interface class SessionGateway {
  Future<GatewaySession> createSession(String userId, String secret);
  Future<GatewaySession?> current();
  Future<GatewaySession> extend(String sessionId);
  Future<void> deleteCurrent();
  void useSessionSecret(String secret);

  /// The signed-in Appwrite account's role labels and status (A03). Throws
  /// when offline or when the account cannot be read.
  Future<GatewayAccount> account();
}

class GatewayAccount {
  const GatewayAccount({required this.labels, this.active = true});
  final List<String> labels;

  /// Appwrite `status`: false = blocked.
  final bool active;
}

class GatewaySession {
  const GatewaySession({
    required this.userId,
    required this.id,
    required this.expiresAt,
    this.secret,
  });
  final String userId;
  final String id;
  final DateTime expiresAt;
  final String? secret;
}

class SdkSessionGateway implements SessionGateway {
  SdkSessionGateway(this._client) : _account = Account(_client);
  final Client _client;
  final Account _account;

  static GatewaySession _wrap(models.Session s) => GatewaySession(
    userId: s.userId,
    id: s.$id,
    expiresAt: DateTime.parse(s.expire),
    secret: s.secret.isEmpty ? null : s.secret,
  );

  @override
  Future<GatewaySession> createSession(String userId, String secret) async =>
      _wrap(await _account.createSession(userId: userId, secret: secret));

  @override
  Future<GatewaySession?> current() async {
    try {
      return _wrap(await _account.getSession(sessionId: 'current'));
    } on AppwriteException catch (e) {
      if (e.code == 401 || e.code == 404) return null;
      rethrow;
    }
  }

  /// Appwrite: PATCH /account/sessions/{id} extends the session's length.
  @override
  Future<GatewaySession> extend(String sessionId) async =>
      _wrap(await _account.updateSession(sessionId: sessionId));

  @override
  Future<void> deleteCurrent() async {
    await _account.deleteSession(sessionId: 'current');
  }

  @override
  void useSessionSecret(String secret) => _client.setSession(secret);

  @override
  Future<GatewayAccount> account() async {
    final u = await _account.get();
    return GatewayAccount(labels: List<String>.of(u.labels), active: u.status);
  }
}

/// Appwrite Flutter SDK adapter. Compiled but never exercised against a live
/// project in tests (owner decision: tests are offline); see
/// [SessionGateway] and `appwrite_auth_test.dart` for the faked paths.
class AppwriteAuthService implements AuthService {
  AppwriteAuthService(
    Client client, {
    required CoreOtpApi otp,
    SessionGateway? gateway,
    SessionVault? vault,
    DateTime Function()? clock,
    this.refreshWindow = const Duration(days: 7),
  }) : _account = Account(client),
       _otp = otp, // ignore: prefer_initializing_formals
       _gateway = gateway ?? SdkSessionGateway(client),
       _vault = vault ?? SessionVault(const FlutterSecureKeyValueStore()),
       _now = clock ?? DateTime.now;

  final Account _account;
  final CoreOtpApi _otp;
  final SessionGateway _gateway;
  final SessionVault _vault;
  final DateTime Function() _now;

  /// Bumped by [signOut]: a refresh that started before it must not save anything.
  int _generation = 0;

  /// Sessions closer to expiry than this are extended on start and resume.
  final Duration refreshWindow;

  @override
  Future<AuthSession?> currentSession() async {
    try {
      final u = await _account.get();
      return AuthSession(userId: u.$id, email: u.email);
    } on AppwriteException catch (e) {
      if (e.code == 401) return null;
      throw AuthException(e.message ?? 'Auth error');
    }
  }

  @override
  Future<AuthSession> signIn(String email, String password) async {
    try {
      final s = await _account.createEmailPasswordSession(
        email: email,
        password: password,
      );
      return AuthSession(userId: s.userId, email: email);
    } on AppwriteException catch (e) {
      throw AuthException(e.message ?? 'Sign-in failed');
    }
  }

  @override
  Future<void> signOut() async {
    _generation++;
    await _vault.clear();
    try {
      await _gateway.deleteCurrent();
    } on AppwriteException catch (e) {
      // Already gone on the server is fine: we are signed out locally.
      if (e.code != 401 && e.code != 404) {
        throw AuthException(e.message ?? 'Sign-out failed');
      }
    }
  }

  @override
  Future<OtpRequestResult> requestOtp(String phoneE164) =>
      _otp.request(phoneE164);

  @override
  Future<AuthSession> verifyOtp(String phoneE164, String code) async {
    final token = await _otp.verify(phoneE164, code);
    final GatewaySession s;
    try {
      s = await _gateway.createSession(token.userId, token.secret);
    } on AppwriteException catch (e) {
      // The one-time token is short-lived; an expired or used one needs a new code.
      if (e.code == 401 || e.code == 404) {
        throw const OtpException(OtpFailure.expired);
      }
      if (e.code == 429) throw const OtpException(OtpFailure.rateLimited);
      throw const OtpException(OtpFailure.serverUnavailable);
    }
    // The core service has just granted this app's role (and refused staff),
    // so that is the best label guess if the account cannot be read right now.
    final acct = await _readAccount(fallback: [_otp.role.name]);
    return _persist(s, maskPhone(phoneE164), acct, isNew: token.isNewUser);
  }

  /// Fresh labels and status, or [fallback] / the cached values when offline.
  Future<_Account> _readAccount({
    required List<String> fallback,
    StoredSession? stored,
  }) async {
    try {
      final a = await _gateway.account();
      return _Account(
        cleanLabels(a.labels).toList(),
        a.active ? AccountStatus.active : AccountStatus.suspended,
      );
    } on Exception {
      return _Account(
        cleanLabels(stored?.labels ?? fallback).toList(),
        accountStatusFrom(stored?.status),
      );
    }
  }

  Future<AuthSession> _persist(
    GatewaySession s,
    String? phoneMasked,
    _Account acct, {
    bool isNew = false,
  }) async {
    await _vault.save(
      StoredSession(
        userId: s.userId,
        sessionId: s.id,
        expiresAt: s.expiresAt,
        phoneMasked: phoneMasked,
        secret: s.secret,
        labels: acct.labels,
        status: acct.status.name,
      ),
    );
    return AuthSession(
      userId: s.userId,
      phoneMasked: phoneMasked,
      expiresAt: s.expiresAt,
      isNewUser: isNew,
      labels: acct.labels,
      status: acct.status,
    );
  }

  @override
  Future<AuthSession?> refreshSession() async {
    final gen = _generation;
    final stored = await _vault.load();
    if (stored == null) return null;
    if (!stored.expiresAt.isAfter(_now())) {
      await _vault.clear();
      return null;
    }
    final secret = stored.secret;
    if (secret != null) _gateway.useSessionSecret(secret);
    try {
      var s = await _gateway.current();
      if (s == null) {
        await _vault.clear(); // revoked or expired server-side
        return null;
      }
      if (gen != _generation) return null;
      if (s.expiresAt.difference(_now()) < refreshWindow) {
        s = await _gateway.extend(s.id);
      }
      final acct = await _readAccount(
        fallback: [_otp.role.name],
        stored: stored,
      );
      // Signed out while this refresh was in flight: save nothing.
      if (gen != _generation) return null;
      return await _persist(s, stored.phoneMasked, acct);
    } on AppwriteException catch (e) {
      if (e.code == 401) {
        await _vault.clear();
        return null;
      }
      // Offline or a server hiccup: keep the stored session, stay signed in.
      return _cached(stored);
    } on Exception {
      return _cached(stored);
    }
  }

  /// The stored session with its cached roles: an offline start routes to
  /// the same experience as the last online one.
  AuthSession _cached(StoredSession stored) => AuthSession(
    userId: stored.userId,
    phoneMasked: stored.phoneMasked,
    expiresAt: stored.expiresAt,
    labels: cleanLabels(stored.labels ?? [_otp.role.name]).toList(),
    status: accountStatusFrom(stored.status),
  );
}

class _Account {
  const _Account(this.labels, this.status);
  final List<String> labels;
  final AccountStatus status;
}
