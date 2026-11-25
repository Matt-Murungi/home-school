// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'draft_database.dart';

// ignore_for_file: type=lint
class $ApplicationDraftRowsTable extends ApplicationDraftRows
    with TableInfo<$ApplicationDraftRowsTable, DraftRow> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $ApplicationDraftRowsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _userIdMeta = const VerificationMeta('userId');
  @override
  late final GeneratedColumn<String> userId = GeneratedColumn<String>(
    'user_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _jsonMeta = const VerificationMeta('json');
  @override
  late final GeneratedColumn<String> json = GeneratedColumn<String>(
    'json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _stepMeta = const VerificationMeta('step');
  @override
  late final GeneratedColumn<int> step = GeneratedColumn<int>(
    'step',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
    defaultValue: const Constant(0),
  );
  static const VerificationMeta _baseRevisionMeta = const VerificationMeta(
    'baseRevision',
  );
  @override
  late final GeneratedColumn<int> baseRevision = GeneratedColumn<int>(
    'base_revision',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
    defaultValue: const Constant(0),
  );
  static const VerificationMeta _dirtyMeta = const VerificationMeta('dirty');
  @override
  late final GeneratedColumn<bool> dirty = GeneratedColumn<bool>(
    'dirty',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("dirty" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  static const VerificationMeta _submitKeyMeta = const VerificationMeta(
    'submitKey',
  );
  @override
  late final GeneratedColumn<String> submitKey = GeneratedColumn<String>(
    'submit_key',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _statusMeta = const VerificationMeta('status');
  @override
  late final GeneratedColumn<String> status = GeneratedColumn<String>(
    'status',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
    defaultValue: const Constant('none'),
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [
    userId,
    json,
    step,
    baseRevision,
    dirty,
    submitKey,
    status,
    updatedAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'application_draft_rows';
  @override
  VerificationContext validateIntegrity(
    Insertable<DraftRow> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('user_id')) {
      context.handle(
        _userIdMeta,
        userId.isAcceptableOrUnknown(data['user_id']!, _userIdMeta),
      );
    } else if (isInserting) {
      context.missing(_userIdMeta);
    }
    if (data.containsKey('json')) {
      context.handle(
        _jsonMeta,
        json.isAcceptableOrUnknown(data['json']!, _jsonMeta),
      );
    } else if (isInserting) {
      context.missing(_jsonMeta);
    }
    if (data.containsKey('step')) {
      context.handle(
        _stepMeta,
        step.isAcceptableOrUnknown(data['step']!, _stepMeta),
      );
    }
    if (data.containsKey('base_revision')) {
      context.handle(
        _baseRevisionMeta,
        baseRevision.isAcceptableOrUnknown(
          data['base_revision']!,
          _baseRevisionMeta,
        ),
      );
    }
    if (data.containsKey('dirty')) {
      context.handle(
        _dirtyMeta,
        dirty.isAcceptableOrUnknown(data['dirty']!, _dirtyMeta),
      );
    }
    if (data.containsKey('submit_key')) {
      context.handle(
        _submitKeyMeta,
        submitKey.isAcceptableOrUnknown(data['submit_key']!, _submitKeyMeta),
      );
    }
    if (data.containsKey('status')) {
      context.handle(
        _statusMeta,
        status.isAcceptableOrUnknown(data['status']!, _statusMeta),
      );
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    } else if (isInserting) {
      context.missing(_updatedAtMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {userId};
  @override
  DraftRow map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return DraftRow(
      userId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}user_id'],
      )!,
      json: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}json'],
      )!,
      step: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}step'],
      )!,
      baseRevision: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}base_revision'],
      )!,
      dirty: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}dirty'],
      )!,
      submitKey: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}submit_key'],
      ),
      status: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}status'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      )!,
    );
  }

  @override
  $ApplicationDraftRowsTable createAlias(String alias) {
    return $ApplicationDraftRowsTable(attachedDatabase, alias);
  }
}

class DraftRow extends DataClass implements Insertable<DraftRow> {
  final String userId;
  final String json;
  final int step;

  /// Server revision this copy was last in sync with.
  final int baseRevision;

  /// Edits not yet accepted by the server.
  final bool dirty;

  /// Idempotency key of a submit in flight: created once, reused on every
  /// retry (also after a kill) so the server can answer "already done".
  final String? submitKey;
  final String status;
  final DateTime updatedAt;
  const DraftRow({
    required this.userId,
    required this.json,
    required this.step,
    required this.baseRevision,
    required this.dirty,
    this.submitKey,
    required this.status,
    required this.updatedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['user_id'] = Variable<String>(userId);
    map['json'] = Variable<String>(json);
    map['step'] = Variable<int>(step);
    map['base_revision'] = Variable<int>(baseRevision);
    map['dirty'] = Variable<bool>(dirty);
    if (!nullToAbsent || submitKey != null) {
      map['submit_key'] = Variable<String>(submitKey);
    }
    map['status'] = Variable<String>(status);
    map['updated_at'] = Variable<DateTime>(updatedAt);
    return map;
  }

  ApplicationDraftRowsCompanion toCompanion(bool nullToAbsent) {
    return ApplicationDraftRowsCompanion(
      userId: Value(userId),
      json: Value(json),
      step: Value(step),
      baseRevision: Value(baseRevision),
      dirty: Value(dirty),
      submitKey: submitKey == null && nullToAbsent
          ? const Value.absent()
          : Value(submitKey),
      status: Value(status),
      updatedAt: Value(updatedAt),
    );
  }

  factory DraftRow.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return DraftRow(
      userId: serializer.fromJson<String>(json['userId']),
      json: serializer.fromJson<String>(json['json']),
      step: serializer.fromJson<int>(json['step']),
      baseRevision: serializer.fromJson<int>(json['baseRevision']),
      dirty: serializer.fromJson<bool>(json['dirty']),
      submitKey: serializer.fromJson<String?>(json['submitKey']),
      status: serializer.fromJson<String>(json['status']),
      updatedAt: serializer.fromJson<DateTime>(json['updatedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'userId': serializer.toJson<String>(userId),
      'json': serializer.toJson<String>(json),
      'step': serializer.toJson<int>(step),
      'baseRevision': serializer.toJson<int>(baseRevision),
      'dirty': serializer.toJson<bool>(dirty),
      'submitKey': serializer.toJson<String?>(submitKey),
      'status': serializer.toJson<String>(status),
      'updatedAt': serializer.toJson<DateTime>(updatedAt),
    };
  }

  DraftRow copyWith({
    String? userId,
    String? json,
    int? step,
    int? baseRevision,
    bool? dirty,
    Value<String?> submitKey = const Value.absent(),
    String? status,
    DateTime? updatedAt,
  }) => DraftRow(
    userId: userId ?? this.userId,
    json: json ?? this.json,
    step: step ?? this.step,
    baseRevision: baseRevision ?? this.baseRevision,
    dirty: dirty ?? this.dirty,
    submitKey: submitKey.present ? submitKey.value : this.submitKey,
    status: status ?? this.status,
    updatedAt: updatedAt ?? this.updatedAt,
  );
  DraftRow copyWithCompanion(ApplicationDraftRowsCompanion data) {
    return DraftRow(
      userId: data.userId.present ? data.userId.value : this.userId,
      json: data.json.present ? data.json.value : this.json,
      step: data.step.present ? data.step.value : this.step,
      baseRevision: data.baseRevision.present
          ? data.baseRevision.value
          : this.baseRevision,
      dirty: data.dirty.present ? data.dirty.value : this.dirty,
      submitKey: data.submitKey.present ? data.submitKey.value : this.submitKey,
      status: data.status.present ? data.status.value : this.status,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('DraftRow(')
          ..write('userId: $userId, ')
          ..write('json: $json, ')
          ..write('step: $step, ')
          ..write('baseRevision: $baseRevision, ')
          ..write('dirty: $dirty, ')
          ..write('submitKey: $submitKey, ')
          ..write('status: $status, ')
          ..write('updatedAt: $updatedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    userId,
    json,
    step,
    baseRevision,
    dirty,
    submitKey,
    status,
    updatedAt,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is DraftRow &&
          other.userId == this.userId &&
          other.json == this.json &&
          other.step == this.step &&
          other.baseRevision == this.baseRevision &&
          other.dirty == this.dirty &&
          other.submitKey == this.submitKey &&
          other.status == this.status &&
          other.updatedAt == this.updatedAt);
}

class ApplicationDraftRowsCompanion extends UpdateCompanion<DraftRow> {
  final Value<String> userId;
  final Value<String> json;
  final Value<int> step;
  final Value<int> baseRevision;
  final Value<bool> dirty;
  final Value<String?> submitKey;
  final Value<String> status;
  final Value<DateTime> updatedAt;
  final Value<int> rowid;
  const ApplicationDraftRowsCompanion({
    this.userId = const Value.absent(),
    this.json = const Value.absent(),
    this.step = const Value.absent(),
    this.baseRevision = const Value.absent(),
    this.dirty = const Value.absent(),
    this.submitKey = const Value.absent(),
    this.status = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  ApplicationDraftRowsCompanion.insert({
    required String userId,
    required String json,
    this.step = const Value.absent(),
    this.baseRevision = const Value.absent(),
    this.dirty = const Value.absent(),
    this.submitKey = const Value.absent(),
    this.status = const Value.absent(),
    required DateTime updatedAt,
    this.rowid = const Value.absent(),
  }) : userId = Value(userId),
       json = Value(json),
       updatedAt = Value(updatedAt);
  static Insertable<DraftRow> custom({
    Expression<String>? userId,
    Expression<String>? json,
    Expression<int>? step,
    Expression<int>? baseRevision,
    Expression<bool>? dirty,
    Expression<String>? submitKey,
    Expression<String>? status,
    Expression<DateTime>? updatedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (userId != null) 'user_id': userId,
      if (json != null) 'json': json,
      if (step != null) 'step': step,
      if (baseRevision != null) 'base_revision': baseRevision,
      if (dirty != null) 'dirty': dirty,
      if (submitKey != null) 'submit_key': submitKey,
      if (status != null) 'status': status,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  ApplicationDraftRowsCompanion copyWith({
    Value<String>? userId,
    Value<String>? json,
    Value<int>? step,
    Value<int>? baseRevision,
    Value<bool>? dirty,
    Value<String?>? submitKey,
    Value<String>? status,
    Value<DateTime>? updatedAt,
    Value<int>? rowid,
  }) {
    return ApplicationDraftRowsCompanion(
      userId: userId ?? this.userId,
      json: json ?? this.json,
      step: step ?? this.step,
      baseRevision: baseRevision ?? this.baseRevision,
      dirty: dirty ?? this.dirty,
      submitKey: submitKey ?? this.submitKey,
      status: status ?? this.status,
      updatedAt: updatedAt ?? this.updatedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (userId.present) {
      map['user_id'] = Variable<String>(userId.value);
    }
    if (json.present) {
      map['json'] = Variable<String>(json.value);
    }
    if (step.present) {
      map['step'] = Variable<int>(step.value);
    }
    if (baseRevision.present) {
      map['base_revision'] = Variable<int>(baseRevision.value);
    }
    if (dirty.present) {
      map['dirty'] = Variable<bool>(dirty.value);
    }
    if (submitKey.present) {
      map['submit_key'] = Variable<String>(submitKey.value);
    }
    if (status.present) {
      map['status'] = Variable<String>(status.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('ApplicationDraftRowsCompanion(')
          ..write('userId: $userId, ')
          ..write('json: $json, ')
          ..write('step: $step, ')
          ..write('baseRevision: $baseRevision, ')
          ..write('dirty: $dirty, ')
          ..write('submitKey: $submitKey, ')
          ..write('status: $status, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $ReferenceCacheTable extends ReferenceCache
    with TableInfo<$ReferenceCacheTable, ReferenceCacheData> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $ReferenceCacheTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<int> id = GeneratedColumn<int>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _jsonMeta = const VerificationMeta('json');
  @override
  late final GeneratedColumn<String> json = GeneratedColumn<String>(
    'json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _fetchedAtMeta = const VerificationMeta(
    'fetchedAt',
  );
  @override
  late final GeneratedColumn<DateTime> fetchedAt = GeneratedColumn<DateTime>(
    'fetched_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [id, json, fetchedAt];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'reference_cache';
  @override
  VerificationContext validateIntegrity(
    Insertable<ReferenceCacheData> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    }
    if (data.containsKey('json')) {
      context.handle(
        _jsonMeta,
        json.isAcceptableOrUnknown(data['json']!, _jsonMeta),
      );
    } else if (isInserting) {
      context.missing(_jsonMeta);
    }
    if (data.containsKey('fetched_at')) {
      context.handle(
        _fetchedAtMeta,
        fetchedAt.isAcceptableOrUnknown(data['fetched_at']!, _fetchedAtMeta),
      );
    } else if (isInserting) {
      context.missing(_fetchedAtMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  ReferenceCacheData map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return ReferenceCacheData(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}id'],
      )!,
      json: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}json'],
      )!,
      fetchedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}fetched_at'],
      )!,
    );
  }

  @override
  $ReferenceCacheTable createAlias(String alias) {
    return $ReferenceCacheTable(attachedDatabase, alias);
  }
}

class ReferenceCacheData extends DataClass
    implements Insertable<ReferenceCacheData> {
  final int id;
  final String json;
  final DateTime fetchedAt;
  const ReferenceCacheData({
    required this.id,
    required this.json,
    required this.fetchedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<int>(id);
    map['json'] = Variable<String>(json);
    map['fetched_at'] = Variable<DateTime>(fetchedAt);
    return map;
  }

  ReferenceCacheCompanion toCompanion(bool nullToAbsent) {
    return ReferenceCacheCompanion(
      id: Value(id),
      json: Value(json),
      fetchedAt: Value(fetchedAt),
    );
  }

  factory ReferenceCacheData.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return ReferenceCacheData(
      id: serializer.fromJson<int>(json['id']),
      json: serializer.fromJson<String>(json['json']),
      fetchedAt: serializer.fromJson<DateTime>(json['fetchedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<int>(id),
      'json': serializer.toJson<String>(json),
      'fetchedAt': serializer.toJson<DateTime>(fetchedAt),
    };
  }

  ReferenceCacheData copyWith({int? id, String? json, DateTime? fetchedAt}) =>
      ReferenceCacheData(
        id: id ?? this.id,
        json: json ?? this.json,
        fetchedAt: fetchedAt ?? this.fetchedAt,
      );
  ReferenceCacheData copyWithCompanion(ReferenceCacheCompanion data) {
    return ReferenceCacheData(
      id: data.id.present ? data.id.value : this.id,
      json: data.json.present ? data.json.value : this.json,
      fetchedAt: data.fetchedAt.present ? data.fetchedAt.value : this.fetchedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('ReferenceCacheData(')
          ..write('id: $id, ')
          ..write('json: $json, ')
          ..write('fetchedAt: $fetchedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(id, json, fetchedAt);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is ReferenceCacheData &&
          other.id == this.id &&
          other.json == this.json &&
          other.fetchedAt == this.fetchedAt);
}

class ReferenceCacheCompanion extends UpdateCompanion<ReferenceCacheData> {
  final Value<int> id;
  final Value<String> json;
  final Value<DateTime> fetchedAt;
  const ReferenceCacheCompanion({
    this.id = const Value.absent(),
    this.json = const Value.absent(),
    this.fetchedAt = const Value.absent(),
  });
  ReferenceCacheCompanion.insert({
    this.id = const Value.absent(),
    required String json,
    required DateTime fetchedAt,
  }) : json = Value(json),
       fetchedAt = Value(fetchedAt);
  static Insertable<ReferenceCacheData> custom({
    Expression<int>? id,
    Expression<String>? json,
    Expression<DateTime>? fetchedAt,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (json != null) 'json': json,
      if (fetchedAt != null) 'fetched_at': fetchedAt,
    });
  }

  ReferenceCacheCompanion copyWith({
    Value<int>? id,
    Value<String>? json,
    Value<DateTime>? fetchedAt,
  }) {
    return ReferenceCacheCompanion(
      id: id ?? this.id,
      json: json ?? this.json,
      fetchedAt: fetchedAt ?? this.fetchedAt,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<int>(id.value);
    }
    if (json.present) {
      map['json'] = Variable<String>(json.value);
    }
    if (fetchedAt.present) {
      map['fetched_at'] = Variable<DateTime>(fetchedAt.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('ReferenceCacheCompanion(')
          ..write('id: $id, ')
          ..write('json: $json, ')
          ..write('fetchedAt: $fetchedAt')
          ..write(')'))
        .toString();
  }
}

abstract class _$DraftDatabase extends GeneratedDatabase {
  _$DraftDatabase(QueryExecutor e) : super(e);
  $DraftDatabaseManager get managers => $DraftDatabaseManager(this);
  late final $ApplicationDraftRowsTable applicationDraftRows =
      $ApplicationDraftRowsTable(this);
  late final $ReferenceCacheTable referenceCache = $ReferenceCacheTable(this);
  @override
  Iterable<TableInfo<Table, Object?>> get allTables =>
      allSchemaEntities.whereType<TableInfo<Table, Object?>>();
  @override
  List<DatabaseSchemaEntity> get allSchemaEntities => [
    applicationDraftRows,
    referenceCache,
  ];
}

typedef $$ApplicationDraftRowsTableCreateCompanionBuilder =
    ApplicationDraftRowsCompanion Function({
      required String userId,
      required String json,
      Value<int> step,
      Value<int> baseRevision,
      Value<bool> dirty,
      Value<String?> submitKey,
      Value<String> status,
      required DateTime updatedAt,
      Value<int> rowid,
    });
typedef $$ApplicationDraftRowsTableUpdateCompanionBuilder =
    ApplicationDraftRowsCompanion Function({
      Value<String> userId,
      Value<String> json,
      Value<int> step,
      Value<int> baseRevision,
      Value<bool> dirty,
      Value<String?> submitKey,
      Value<String> status,
      Value<DateTime> updatedAt,
      Value<int> rowid,
    });

class $$ApplicationDraftRowsTableFilterComposer
    extends Composer<_$DraftDatabase, $ApplicationDraftRowsTable> {
  $$ApplicationDraftRowsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get userId => $composableBuilder(
    column: $table.userId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get json => $composableBuilder(
    column: $table.json,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get step => $composableBuilder(
    column: $table.step,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get baseRevision => $composableBuilder(
    column: $table.baseRevision,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get dirty => $composableBuilder(
    column: $table.dirty,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get submitKey => $composableBuilder(
    column: $table.submitKey,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get status => $composableBuilder(
    column: $table.status,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$ApplicationDraftRowsTableOrderingComposer
    extends Composer<_$DraftDatabase, $ApplicationDraftRowsTable> {
  $$ApplicationDraftRowsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get userId => $composableBuilder(
    column: $table.userId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get json => $composableBuilder(
    column: $table.json,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get step => $composableBuilder(
    column: $table.step,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get baseRevision => $composableBuilder(
    column: $table.baseRevision,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get dirty => $composableBuilder(
    column: $table.dirty,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get submitKey => $composableBuilder(
    column: $table.submitKey,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get status => $composableBuilder(
    column: $table.status,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$ApplicationDraftRowsTableAnnotationComposer
    extends Composer<_$DraftDatabase, $ApplicationDraftRowsTable> {
  $$ApplicationDraftRowsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get userId =>
      $composableBuilder(column: $table.userId, builder: (column) => column);

  GeneratedColumn<String> get json =>
      $composableBuilder(column: $table.json, builder: (column) => column);

  GeneratedColumn<int> get step =>
      $composableBuilder(column: $table.step, builder: (column) => column);

  GeneratedColumn<int> get baseRevision => $composableBuilder(
    column: $table.baseRevision,
    builder: (column) => column,
  );

  GeneratedColumn<bool> get dirty =>
      $composableBuilder(column: $table.dirty, builder: (column) => column);

  GeneratedColumn<String> get submitKey =>
      $composableBuilder(column: $table.submitKey, builder: (column) => column);

  GeneratedColumn<String> get status =>
      $composableBuilder(column: $table.status, builder: (column) => column);

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);
}

class $$ApplicationDraftRowsTableTableManager
    extends
        RootTableManager<
          _$DraftDatabase,
          $ApplicationDraftRowsTable,
          DraftRow,
          $$ApplicationDraftRowsTableFilterComposer,
          $$ApplicationDraftRowsTableOrderingComposer,
          $$ApplicationDraftRowsTableAnnotationComposer,
          $$ApplicationDraftRowsTableCreateCompanionBuilder,
          $$ApplicationDraftRowsTableUpdateCompanionBuilder,
          (
            DraftRow,
            BaseReferences<
              _$DraftDatabase,
              $ApplicationDraftRowsTable,
              DraftRow
            >,
          ),
          DraftRow,
          PrefetchHooks Function()
        > {
  $$ApplicationDraftRowsTableTableManager(
    _$DraftDatabase db,
    $ApplicationDraftRowsTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$ApplicationDraftRowsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$ApplicationDraftRowsTableOrderingComposer(
                $db: db,
                $table: table,
              ),
          createComputedFieldComposer: () =>
              $$ApplicationDraftRowsTableAnnotationComposer(
                $db: db,
                $table: table,
              ),
          updateCompanionCallback:
              ({
                Value<String> userId = const Value.absent(),
                Value<String> json = const Value.absent(),
                Value<int> step = const Value.absent(),
                Value<int> baseRevision = const Value.absent(),
                Value<bool> dirty = const Value.absent(),
                Value<String?> submitKey = const Value.absent(),
                Value<String> status = const Value.absent(),
                Value<DateTime> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => ApplicationDraftRowsCompanion(
                userId: userId,
                json: json,
                step: step,
                baseRevision: baseRevision,
                dirty: dirty,
                submitKey: submitKey,
                status: status,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String userId,
                required String json,
                Value<int> step = const Value.absent(),
                Value<int> baseRevision = const Value.absent(),
                Value<bool> dirty = const Value.absent(),
                Value<String?> submitKey = const Value.absent(),
                Value<String> status = const Value.absent(),
                required DateTime updatedAt,
                Value<int> rowid = const Value.absent(),
              }) => ApplicationDraftRowsCompanion.insert(
                userId: userId,
                json: json,
                step: step,
                baseRevision: baseRevision,
                dirty: dirty,
                submitKey: submitKey,
                status: status,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map(
                (e) => (
                  e.readTable<$ApplicationDraftRowsTable, DraftRow>(table),
                  BaseReferences<
                    _$DraftDatabase,
                    $ApplicationDraftRowsTable,
                    DraftRow
                  >(db, table, e),
                ),
              )
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$ApplicationDraftRowsTableProcessedTableManager =
    ProcessedTableManager<
      _$DraftDatabase,
      $ApplicationDraftRowsTable,
      DraftRow,
      $$ApplicationDraftRowsTableFilterComposer,
      $$ApplicationDraftRowsTableOrderingComposer,
      $$ApplicationDraftRowsTableAnnotationComposer,
      $$ApplicationDraftRowsTableCreateCompanionBuilder,
      $$ApplicationDraftRowsTableUpdateCompanionBuilder,
      (
        DraftRow,
        BaseReferences<_$DraftDatabase, $ApplicationDraftRowsTable, DraftRow>,
      ),
      DraftRow,
      PrefetchHooks Function()
    >;
typedef $$ReferenceCacheTableCreateCompanionBuilder =
    ReferenceCacheCompanion Function({
      Value<int> id,
      required String json,
      required DateTime fetchedAt,
    });
typedef $$ReferenceCacheTableUpdateCompanionBuilder =
    ReferenceCacheCompanion Function({
      Value<int> id,
      Value<String> json,
      Value<DateTime> fetchedAt,
    });

class $$ReferenceCacheTableFilterComposer
    extends Composer<_$DraftDatabase, $ReferenceCacheTable> {
  $$ReferenceCacheTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<int> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get json => $composableBuilder(
    column: $table.json,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get fetchedAt => $composableBuilder(
    column: $table.fetchedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$ReferenceCacheTableOrderingComposer
    extends Composer<_$DraftDatabase, $ReferenceCacheTable> {
  $$ReferenceCacheTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<int> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get json => $composableBuilder(
    column: $table.json,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get fetchedAt => $composableBuilder(
    column: $table.fetchedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$ReferenceCacheTableAnnotationComposer
    extends Composer<_$DraftDatabase, $ReferenceCacheTable> {
  $$ReferenceCacheTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<int> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get json =>
      $composableBuilder(column: $table.json, builder: (column) => column);

  GeneratedColumn<DateTime> get fetchedAt =>
      $composableBuilder(column: $table.fetchedAt, builder: (column) => column);
}

class $$ReferenceCacheTableTableManager
    extends
        RootTableManager<
          _$DraftDatabase,
          $ReferenceCacheTable,
          ReferenceCacheData,
          $$ReferenceCacheTableFilterComposer,
          $$ReferenceCacheTableOrderingComposer,
          $$ReferenceCacheTableAnnotationComposer,
          $$ReferenceCacheTableCreateCompanionBuilder,
          $$ReferenceCacheTableUpdateCompanionBuilder,
          (
            ReferenceCacheData,
            BaseReferences<
              _$DraftDatabase,
              $ReferenceCacheTable,
              ReferenceCacheData
            >,
          ),
          ReferenceCacheData,
          PrefetchHooks Function()
        > {
  $$ReferenceCacheTableTableManager(
    _$DraftDatabase db,
    $ReferenceCacheTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$ReferenceCacheTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$ReferenceCacheTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$ReferenceCacheTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<int> id = const Value.absent(),
                Value<String> json = const Value.absent(),
                Value<DateTime> fetchedAt = const Value.absent(),
              }) => ReferenceCacheCompanion(
                id: id,
                json: json,
                fetchedAt: fetchedAt,
              ),
          createCompanionCallback:
              ({
                Value<int> id = const Value.absent(),
                required String json,
                required DateTime fetchedAt,
              }) => ReferenceCacheCompanion.insert(
                id: id,
                json: json,
                fetchedAt: fetchedAt,
              ),
          withReferenceMapper: (p0) => p0
              .map(
                (e) => (
                  e.readTable<$ReferenceCacheTable, ReferenceCacheData>(table),
                  BaseReferences<
                    _$DraftDatabase,
                    $ReferenceCacheTable,
                    ReferenceCacheData
                  >(db, table, e),
                ),
              )
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$ReferenceCacheTableProcessedTableManager =
    ProcessedTableManager<
      _$DraftDatabase,
      $ReferenceCacheTable,
      ReferenceCacheData,
      $$ReferenceCacheTableFilterComposer,
      $$ReferenceCacheTableOrderingComposer,
      $$ReferenceCacheTableAnnotationComposer,
      $$ReferenceCacheTableCreateCompanionBuilder,
      $$ReferenceCacheTableUpdateCompanionBuilder,
      (
        ReferenceCacheData,
        BaseReferences<
          _$DraftDatabase,
          $ReferenceCacheTable,
          ReferenceCacheData
        >,
      ),
      ReferenceCacheData,
      PrefetchHooks Function()
    >;

class $DraftDatabaseManager {
  final _$DraftDatabase _db;
  $DraftDatabaseManager(this._db);
  $$ApplicationDraftRowsTableTableManager get applicationDraftRows =>
      $$ApplicationDraftRowsTableTableManager(_db, _db.applicationDraftRows);
  $$ReferenceCacheTableTableManager get referenceCache =>
      $$ReferenceCacheTableTableManager(_db, _db.referenceCache);
}
