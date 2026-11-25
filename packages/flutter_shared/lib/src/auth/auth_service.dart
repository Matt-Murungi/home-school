class AuthSession {
  const AuthSession({required this.userId, required this.email});
  final String userId;
  final String email;
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
}

class FakeAuthService implements AuthService {
  FakeAuthService({Map<String, String> users = const {}})
    : _users = users; // ignore: prefer_initializing_formals
  final Map<String, String> _users;
  AuthSession? _session;

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
}
