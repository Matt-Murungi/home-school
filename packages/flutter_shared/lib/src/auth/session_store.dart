import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Where the session lives on the device: Android Keystore-backed encrypted
/// storage / iOS Keychain (`flutter_secure_storage`). Never SharedPreferences.
abstract interface class SecureKeyValueStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> delete(String key);
}

class FlutterSecureKeyValueStore implements SecureKeyValueStore {
  const FlutterSecureKeyValueStore([
    this._s = const FlutterSecureStorage(
      iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock),
    ),
  ]);
  final FlutterSecureStorage _s;

  @override
  Future<String?> read(String key) => _s.read(key: key);
  @override
  Future<void> write(String key, String value) =>
      _s.write(key: key, value: value);
  @override
  Future<void> delete(String key) => _s.delete(key: key);
}

class InMemorySecureStore implements SecureKeyValueStore {
  final values = <String, String>{};
  @override
  Future<String?> read(String key) async => values[key];
  @override
  Future<void> write(String key, String value) async => values[key] = value;
  @override
  Future<void> delete(String key) async => values.remove(key);
}

/// What we keep about the signed-in session. [secret] is the Appwrite
/// session secret when the server returns one to the client (unverified:
/// needs a real-device check); without it the Appwrite SDK keeps its own
/// cookie and only the identifiers are stored.
class StoredSession {
  const StoredSession({
    required this.userId,
    required this.sessionId,
    required this.expiresAt,
    this.phoneMasked,
    this.secret,
    this.labels,
    this.status = 'active',
  });
  final String userId;
  final String sessionId;
  final DateTime expiresAt;
  final String? phoneMasked;
  final String? secret;

  /// Role labels and account status as last fetched (A03): lets the app start
  /// offline and still route to the right experience. Null = stored by an
  /// older version (A01), which the apps treat as "the role this app signs in".
  final List<String>? labels;
  final String status;

  Map<String, Object?> toJson() => {
    'userId': userId,
    'sessionId': sessionId,
    'expiresAt': expiresAt.toUtc().toIso8601String(),
    'phoneMasked': phoneMasked,
    'secret': secret,
    'labels': labels,
    'status': status,
  };

  static StoredSession? fromJson(Object? j) {
    if (j is! Map) return null;
    final u = j['userId'];
    final s = j['sessionId'];
    final e = DateTime.tryParse('${j['expiresAt']}');
    if (u is! String || s is! String || e == null) return null;
    return StoredSession(
      userId: u,
      sessionId: s,
      expiresAt: e,
      phoneMasked: j['phoneMasked'] as String?,
      secret: j['secret'] as String?,
      labels: j['labels'] is List
          ? [
              for (final l in j['labels'] as List)
                if (l is String) l,
            ]
          : null,
      status: j['status'] is String ? j['status'] as String : 'active',
    );
  }
}

class SessionVault {
  SessionVault(this._store);
  final SecureKeyValueStore _store;
  static const _key = 'hs.session.v1';

  Future<void> save(StoredSession s) =>
      _store.write(_key, jsonEncode(s.toJson()));

  /// A corrupt or unreadable entry is treated as signed out (and cleared).
  Future<StoredSession?> load() async {
    try {
      final raw = await _store.read(_key);
      if (raw == null) return null;
      final s = StoredSession.fromJson(jsonDecode(raw));
      if (s == null) await _store.delete(_key);
      return s;
    } on FormatException {
      await _store.delete(_key);
      return null;
    }
  }

  Future<void> clear() => _store.delete(_key);
}
