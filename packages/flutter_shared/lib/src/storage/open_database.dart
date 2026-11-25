import 'dart:io';

import 'package:drift/native.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import 'app_database.dart';

/// Opens the on-device database. Used by app main(); tests use in-memory.
Future<AppDatabase> openAppDatabase() async {
  final dir = await getApplicationSupportDirectory();
  return AppDatabase(
    NativeDatabase.createInBackground(File(p.join(dir.path, 'hs.sqlite'))),
  );
}
