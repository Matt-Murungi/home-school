import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'api_client.dart';

/// `dart:io` implementation of [ApiClient] for the core service. Returns the
/// response for every HTTP status (the caller maps error bodies); network
/// failures surface as [SocketException] / [TimeoutException].
class HttpApiClient implements ApiClient {
  HttpApiClient(
    this.baseUrl, {
    this.timeout = const Duration(seconds: 15),
    HttpClient? client,
  }) : _client = client ?? HttpClient();

  final String baseUrl;
  final Duration timeout;
  final HttpClient _client;

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    final uri = Uri.parse('$baseUrl${request.path}');
    final req = await _client.openUrl(request.method, uri).timeout(timeout);
    req.headers
      ..set(HttpHeaders.acceptHeader, 'application/json')
      ..set(HttpHeaders.contentTypeHeader, 'application/json');
    if (request.body != null) req.write(jsonEncode(request.body));
    final res = await req.close().timeout(timeout);
    final text = await res.transform(utf8.decoder).join().timeout(timeout);
    Map<String, Object?> body = const {};
    try {
      final j = jsonDecode(text);
      if (j is Map<String, Object?>) body = j;
    } on FormatException {
      // Non-JSON error page from a proxy: status code alone is informative.
    }
    return ApiResponse(res.statusCode, body);
  }

  void close() => _client.close(force: true);
}
