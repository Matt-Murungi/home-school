import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

void main() {
  var now = DateTime.utc(2026, 11, 26, 9);
  var minted = 0;
  late CachedTokenProvider cache;
  Future<String?> mint() async => 'jwt-${++minted}';

  setUp(() {
    now = DateTime.utc(2026, 11, 26, 9);
    minted = 0;
    cache = CachedTokenProvider(mint, clock: () => now);
  });

  test('one token serves many requests inside the window', () async {
    expect(await cache(), 'jwt-1');
    now = now.add(const Duration(minutes: 5));
    expect(await cache(), 'jwt-1');
    expect(minted, 1);
  });

  test(
    'a new token is minted before the 10 minute mark minus the margin',
    () async {
      await cache();
      now = now.add(const Duration(minutes: 9, seconds: 29));
      expect(await cache(), 'jwt-1');
      now = now.add(const Duration(seconds: 2)); // 9:31 > 10:00 - 30 s
      expect(await cache(), 'jwt-2');
    },
  );

  test(
    'concurrent callers share one mint (Appwrite rate-limits JWT creation)',
    () async {
      final all = await Future.wait([cache(), cache(), cache(), cache()]);
      expect(all.toSet(), {'jwt-1'});
      expect(minted, 1);
    },
  );

  test('a failure is not cached and the next call tries again', () async {
    var fail = true;
    final c = CachedTokenProvider(() async {
      if (fail) throw StateError('network');
      return 'ok';
    }, clock: () => now);
    await expectLater(c(), throwsStateError);
    fail = false;
    expect(await c(), 'ok');
  });

  test('null or empty tokens are not cached', () async {
    final c = CachedTokenProvider(
      () async => ++minted == 1 ? null : 'real',
      clock: () => now,
    );
    expect(await c(), isNull);
    expect(await c(), 'real');
  });

  test('invalidate drops the token; an invalidate during a fetch discards that fetch', () async {
    await cache();
    cache.invalidate();
    expect(await cache(), 'jwt-2');
    final slow = CachedTokenProvider(() async {
      await Future<void>.delayed(const Duration(milliseconds: 10));
      return 'jwt-${++minted}';
    }, clock: () => now);
    final f = slow();
    slow.invalidate();
    await f;
    expect(await slow(), isNot('jwt-3')); // the in-flight token was not kept
  });
}
