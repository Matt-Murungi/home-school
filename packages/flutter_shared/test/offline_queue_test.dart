import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

void main() {
  late AppDatabase db;
  late OfflineQueue q;
  setUp(() {
    db = AppDatabase(NativeDatabase.memory());
    q = OfflineQueue(db);
  });
  tearDown(() => db.close());

  test('starts empty', () async {
    expect(await q.count(), 0);
    expect(await q.next(), isNull);
  });

  test('is FIFO and removes completed items', () async {
    await q.enqueue('check_in', '{"s":1}');
    await q.enqueue('log_session', '{"s":2}');
    expect(await q.count(), 2);
    final first = (await q.next())!;
    expect(first.kind, 'check_in');
    await q.complete(first.id);
    expect((await q.next())!.kind, 'log_session');
    expect(await q.count(), 1);
  });

  test('failure increments attempts but keeps the item', () async {
    final id = await q.enqueue('check_in', '{}');
    await q.markFailed(id);
    await q.markFailed(id);
    expect((await q.next())!.attempts, 2);
  });

  test('watchCount emits changes (offline banner source)', () async {
    final events = <int>[];
    final sub = q.watchCount().listen(events.add);
    await q.enqueue('a', '{}');
    await Future<void>.delayed(const Duration(milliseconds: 50));
    await sub.cancel();
    expect(events.last, 1);
  });
}
