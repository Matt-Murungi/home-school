import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

void main() {
  late HttpServer server;
  late HttpApiClient client;
  final seen = <String>[];

  setUp(() async {
    seen.clear();
    server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((req) async {
      final body = await utf8.decoder.bind(req).join();
      seen.add('${req.method} ${req.uri.path} $body');
      if (req.uri.path == '/ok') {
        req.response
          ..statusCode = 200
          ..write(jsonEncode({'hello': 'world'}));
      } else if (req.uri.path == '/limited') {
        req.response
          ..statusCode = 429
          ..write(
            jsonEncode({
              'error': {'code': 'rate_limited'},
            }),
          );
      } else {
        req.response
          ..statusCode = 502
          ..write('<html>bad gateway</html>');
      }
      await req.response.close();
    });
    client = HttpApiClient('http://127.0.0.1:${server.port}');
  });
  tearDown(() async {
    client.close();
    await server.close(force: true);
  });

  test('POSTs JSON and parses the JSON reply', () async {
    final r = await client.send(
      const ApiRequest('POST', '/ok', body: {'a': 1}),
    );
    expect(r.statusCode, 200);
    expect(r.body['hello'], 'world');
    expect(seen.single, 'POST /ok {"a":1}');
  });

  test('error statuses are returned, not thrown', () async {
    final r = await client.send(const ApiRequest('POST', '/limited'));
    expect(r.statusCode, 429);
    expect((r.body['error'] as Map)['code'], 'rate_limited');
  });

  test('non-JSON error pages give an empty body and the status', () async {
    final r = await client.send(const ApiRequest('GET', '/other'));
    expect(r.statusCode, 502);
    expect(r.body, isEmpty);
  });

  test(
    'an unreachable host throws SocketException (mapped to offline)',
    () async {
      final dead = HttpApiClient('http://127.0.0.1:1');
      await expectLater(
        dead.send(const ApiRequest('GET', '/x')),
        throwsA(isA<SocketException>()),
      );
      dead.close();
    },
  );
}
