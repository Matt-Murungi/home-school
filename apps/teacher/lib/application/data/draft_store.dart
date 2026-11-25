import 'dart:convert';

import 'package:drift/drift.dart';

import '../model/application_draft.dart';
import '../model/reference.dart';
import 'draft_database.dart';

class LocalDraft {
  const LocalDraft({
    required this.draft,
    required this.step,
    required this.baseRevision,
    required this.dirty,
    required this.status,
    this.submitKey,
  });
  final ApplicationDraft draft;
  final int step;
  final int baseRevision;
  final bool dirty;
  final String status;
  final String? submitKey;
}

/// Reads and writes the local working copy of the application (Drift).
class DraftStore {
  DraftStore(this._db, {DateTime Function()? clock})
    : _clock = clock ?? DateTime.now;
  final DraftDatabase _db;
  final DateTime Function() _clock;

  Future<LocalDraft?> load(String userId) async {
    final row = await (_db.select(
      _db.applicationDraftRows,
    )..where((t) => t.userId.equals(userId))).getSingleOrNull();
    if (row == null) return null;
    ApplicationDraft draft;
    try {
      draft = ApplicationDraft.fromJson(jsonDecode(row.json));
    } on Object {
      // A corrupt row must not lock the teacher out: start from the server.
      return null;
    }
    return LocalDraft(
      draft: draft,
      step: row.step,
      baseRevision: row.baseRevision,
      dirty: row.dirty,
      status: row.status,
      submitKey: row.submitKey,
    );
  }

  Future<void> save(
    String userId, {
    required ApplicationDraft draft,
    required int step,
    required int baseRevision,
    required bool dirty,
    required String status,
    String? submitKey,
    bool keepSubmitKey = true,
  }) async {
    final existing = keepSubmitKey ? (await load(userId))?.submitKey : null;
    await _db
        .into(_db.applicationDraftRows)
        .insertOnConflictUpdate(
          ApplicationDraftRowsCompanion.insert(
            userId: userId,
            json: jsonEncode(draft.toJson()),
            step: Value(step),
            baseRevision: Value(baseRevision),
            dirty: Value(dirty),
            status: Value(status),
            submitKey: Value(submitKey ?? existing),
            updatedAt: _clock(),
          ),
        );
  }

  Future<void> clearSubmitKey(String userId) async {
    await (_db.update(_db.applicationDraftRows)
          ..where((t) => t.userId.equals(userId)))
        .write(const ApplicationDraftRowsCompanion(submitKey: Value(null)));
  }

  Future<void> delete(String userId) async {
    await (_db.delete(
      _db.applicationDraftRows,
    )..where((t) => t.userId.equals(userId))).go();
  }

  Future<void> saveReference(ReferenceData data) => _db
      .into(_db.referenceCache)
      .insertOnConflictUpdate(
        ReferenceCacheCompanion.insert(
          id: const Value(1),
          json: jsonEncode(data.toJson()),
          fetchedAt: _clock(),
        ),
      );

  Future<ReferenceData?> loadReference() async {
    final row = await (_db.select(
      _db.referenceCache,
    )..where((t) => t.id.equals(1))).getSingleOrNull();
    if (row == null) return null;
    try {
      return ReferenceData.fromJson(
        (jsonDecode(row.json) as Map).cast<String, Object?>(),
      );
    } on Object {
      return null;
    }
  }
}
