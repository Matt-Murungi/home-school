import 'package:appwrite/appwrite.dart';

import 'auth_service.dart';

/// Appwrite Flutter SDK adapter. Compiled but never exercised against a live
/// project in tests (owner decision: tests are offline).
class AppwriteAuthService implements AuthService {
  AppwriteAuthService(Client client) : _account = Account(client);
  final Account _account;

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
    try {
      await _account.deleteSession(sessionId: 'current');
    } on AppwriteException catch (e) {
      throw AuthException(e.message ?? 'Sign-out failed');
    }
  }
}
