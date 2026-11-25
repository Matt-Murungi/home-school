import 'package:drift/drift.dart';

import 'app_database.dart';

/// Placeholder offline queue (NFR offline-first). Replay logic comes with the
/// features that enqueue actions (check-in, session logs).
class OfflineQueue {
  OfflineQueue(this._db);
  final AppDatabase _db;

  Future<int> enqueue(String kind, String payload) => _db
      .into(_db.queuedActions)
      .insert(QueuedActionsCompanion.insert(kind: kind, payload: payload));

  Future<QueuedAction?> next() =>
      (_db.select(_db.queuedActions)
            ..orderBy([(t) => OrderingTerm.asc(t.id)])
            ..limit(1))
          .getSingleOrNull();

  Future<void> complete(int id) =>
      (_db.delete(_db.queuedActions)..where((t) => t.id.equals(id))).go();

  Future<void> markFailed(int id) => _db.customUpdate(
    'UPDATE queued_actions SET attempts = attempts + 1 WHERE id = ?',
    variables: [Variable.withInt(id)],
    updates: {_db.queuedActions},
  );

  Future<int> count() async =>
      (await _db.select(_db.queuedActions).get()).length;

  Stream<int> watchCount() =>
      _db.select(_db.queuedActions).watch().map((r) => r.length);
}
