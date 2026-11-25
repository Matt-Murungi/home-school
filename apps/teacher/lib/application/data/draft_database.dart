import 'dart:io';

import 'package:drift/drift.dart';
import 'package:drift/native.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

part 'draft_database.g.dart';

/// One row per signed-in user: the working copy of the application on this
/// phone. Kept across app kills; the server copy is reconciled by revision.
@DataClassName('DraftRow')
class ApplicationDraftRows extends Table {
  TextColumn get userId => text()();
  TextColumn get json => text()();
  IntColumn get step => integer().withDefault(const Constant(0))();

  /// Server revision this copy was last in sync with.
  IntColumn get baseRevision => integer().withDefault(const Constant(0))();

  /// Edits not yet accepted by the server.
  BoolColumn get dirty => boolean().withDefault(const Constant(false))();

  /// Idempotency key of a submit in flight: created once, reused on every
  /// retry (also after a kill) so the server can answer "already done".
  TextColumn get submitKey => text().nullable()();
  TextColumn get status => text().withDefault(const Constant('none'))();
  DateTimeColumn get updatedAt => dateTime()();

  @override
  Set<Column> get primaryKey => {userId};
}

/// Last catalogue and zones the server sent (single row, id 1).
class ReferenceCache extends Table {
  IntColumn get id => integer()();
  TextColumn get json => text()();
  DateTimeColumn get fetchedAt => dateTime()();

  @override
  Set<Column> get primaryKey => {id};
}

@DriftDatabase(tables: [ApplicationDraftRows, ReferenceCache])
class DraftDatabase extends _$DraftDatabase {
  DraftDatabase(super.e);

  @override
  int get schemaVersion => 1;
}

Future<DraftDatabase> openDraftDatabase() async {
  final dir = await getApplicationSupportDirectory();
  return DraftDatabase(
    NativeDatabase.createInBackground(
      File(p.join(dir.path, 'teacher_application.sqlite')),
    ),
  );
}
