/// Minimal HTTP-ish contract for calling the core service (F03). The real
/// implementation arrives with the first feature that needs it; tests use
/// [FakeApiClient].
class ApiRequest {
  const ApiRequest(
    this.method,
    this.path, {
    this.body,
    this.headers = const {},
  });
  final String method;
  final String path;
  final Map<String, Object?>? body;

  /// Extra request headers (for example `Idempotency-Key`).
  final Map<String, String> headers;
}

class ApiResponse {
  const ApiResponse(this.statusCode, this.body);
  final int statusCode;
  final Map<String, Object?> body;
}

class ApiException implements Exception {
  const ApiException(this.statusCode, [this.message = '']);
  final int statusCode;
  final String message;
  @override
  String toString() => 'ApiException($statusCode $message)';
}

abstract interface class ApiClient {
  Future<ApiResponse> send(ApiRequest request);
}

class FakeApiClient implements ApiClient {
  final _stubs = <String, ApiResponse>{};
  final _errors = <String, int>{};
  final requests = <ApiRequest>[];

  void stub(
    String method,
    String path,
    Map<String, Object?> body, {
    int status = 200,
  }) => _stubs['$method $path'] = ApiResponse(status, body);

  void stubError(String method, String path, int status) =>
      _errors['$method $path'] = status;

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final key = '${request.method} ${request.path}';
    final err = _errors[key];
    if (err != null) throw ApiException(err, 'stubbed failure');
    final r = _stubs[key];
    if (r == null) throw const ApiException(404, 'no stub');
    return r;
  }
}
