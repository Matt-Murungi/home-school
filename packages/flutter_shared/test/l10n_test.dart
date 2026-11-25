import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('every widget string comes from the ARB (no literal Text) - NFR-13', () {
    final literal = RegExp(r'''Text\(\s*['"]''');
    final offenders = [
      for (final f in Directory(
        'lib/src',
      ).listSync(recursive: true).whereType<File>())
        if (f.path.endsWith('.dart') &&
            !f.path.contains('/gen/') &&
            literal.hasMatch(f.readAsStringSync()))
          f.path,
    ];
    expect(offenders, isEmpty);
  });

  test('ARB keys are documented in the template', () {
    expect(File('lib/src/l10n/app_en.arb').existsSync(), isTrue);
    expect(File('l10n.yaml').readAsStringSync(), contains('app_en.arb'));
  });
}
