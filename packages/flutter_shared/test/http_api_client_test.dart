import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

void main() {
  late HttpServer server;
  late HttpApiClient client;
  final seen = <String>[];
  final headersSeen = <Map<String, String>>[];

  setUp(() async {
    seen.clear();
    headersSeen.clear();
    server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((req) async {
      final body = await utf8.decoder.bind(req).join();
      headersSeen.add({
        'auth': req.headers.value('authorization') ?? '',
        'idem': req.headers.value('idempotency-key') ?? '',
      });
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

  test(
    'sends extra headers and a bearer token when a provider is set',
    () async {
      final authed = HttpApiClient(
        'http://127.0.0.1:${server.port}',
        bearerToken: () async => 'jwt-123',
      );
      addTearDown(authed.close);
      await authed.send(
        const ApiRequest('POST', '/ok', headers: {'Idempotency-Key': 'k-1'}),
      );
      expect(headersSeen.last, {'auth': 'Bearer jwt-123', 'idem': 'k-1'});
      await client.send(const ApiRequest('GET', '/ok'));
      expect(headersSeen.last, {'auth': '', 'idem': ''}); // no token, no extras
    },
  );

  test(
    'a 401 drops the cached token and retries once with a fresh one',
    () async {
      var n = 0;
      final cache = CachedTokenProvider(() async => 'tok-${++n}');
      final s2 = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final auths = <String>[];
      s2.listen((req) async {
        auths.add(req.headers.value('authorization') ?? '');
        req.response.statusCode = auths.length == 1 ? 401 : 200;
        req.response.write('{}');
        await req.response.close();
      });
      final c = HttpApiClient(
        'http://127.0.0.1:${s2.port}',
        bearerToken: cache.call,
        invalidateToken: cache.invalidate,
      );
      addTearDown(() async {
        c.close();
        await s2.close(force: true);
      });
      final r = await c.send(const ApiRequest('GET', '/x'));
      expect(r.statusCode, 200);
      expect(auths, ['Bearer tok-1', 'Bearer tok-2']);
    },
  );
}
