import 'dart:io';

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

  test('queue, order and attempts survive a restart (file database)', () async {
    final dir = Directory.systemTemp.createTempSync('hs_queue');
    addTearDown(() => dir.deleteSync(recursive: true));
    final file = File('${dir.path}/hs.sqlite');

    final first = AppDatabase(NativeDatabase(file));
    final q1 = OfflineQueue(first);
    final a = await q1.enqueue('check_in', '{"n":1}');
    await q1.enqueue('log_session', '{"n":2}');
    await q1.markFailed(a);
    await first.close();

    final second = AppDatabase(NativeDatabase(file));
    addTearDown(second.close);
    final q2 = OfflineQueue(second);
    expect(await q2.count(), 2);
    final head = (await q2.next())!;
    expect(head.kind, 'check_in');
    expect(head.attempts, 1);
    await q2.complete(head.id);
    expect((await q2.next())!.kind, 'log_session');
  });
}
