import 'dart:async';

/// Caches a short-lived bearer token (the Appwrite JWT). Appwrite rate-limits
/// JWT creation, so every request must not mint a new one.
///
/// A token is reused for [reuseFor] (default 10 minutes; Appwrite JWTs live
/// 15) minus [margin], so a token is never used close to its expiry. Concurrent
/// callers share one in-flight fetch. Failures are not cached. [invalidate]
/// drops the token (call it after a 401).
class CachedTokenProvider {
  CachedTokenProvider(
    this._fetch, {
    this.reuseFor = const Duration(minutes: 10),
    this.margin = const Duration(seconds: 30),
    DateTime Function()? clock,
  }) : _clock = clock ?? DateTime.now;

  final Future<String?> Function() _fetch;
  final Duration reuseFor;
  final Duration margin;
  final DateTime Function() _clock;

  String? _token;
  DateTime? _fetchedAt;
  Future<String?>? _inFlight;
  int _generation = 0;

  bool get _fresh {
    if (_token == null || _fetchedAt == null) return false;
    final age = _clock().difference(_fetchedAt!);
    // A clock that jumped backwards gives a negative age: not trusted.
    return !age.isNegative && age < reuseFor - margin;
  }

  Future<String?> call() {
    if (_fresh) return Future.value(_token);
    return _inFlight ??= _refresh();
  }

  Future<String?> _refresh() async {
    final gen = _generation;
    try {
      final t = await _fetch();
      // An invalidate() during the fetch means this token may be the bad one.
      if (t != null && t.isNotEmpty && gen == _generation) {
        _token = t;
        _fetchedAt = _clock();
      }
      return t;
    } finally {
      _inFlight = null;
    }
  }

  void invalidate() {
    _generation++;
    _token = null;
    _fetchedAt = null;
  }
}
