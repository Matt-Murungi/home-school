import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

/// Same cases as services/core/tests/test_otp_phone.py.
void main() {
  final cases = jsonDecode(
    File('test/fixtures/ug_phone_cases.json').readAsStringSync(),
  ) as Map<String, dynamic>;

  group('normalizeUgPhone (shared contract fixture)', () {
    for (final c in cases['valid'] as List) {
      test('accepts ${jsonEncode(c[0])}', () {
        expect(normalizeUgPhone(c[0] as String), c[1]);
      });
    }
    for (final raw in cases['invalid'] as List) {
      test('rejects ${jsonEncode(raw)}', () {
        expect(normalizeUgPhone(raw as String), isNull);
      });
    }
    test('huge input is rejected', () {
      expect(normalizeUgPhone('0' * 100000), isNull);
    });
  });

  test('maskPhone hides the middle digits', () {
    expect(maskPhone('+256772123456'), '+256 *** *** 456');
  });

  group('UgPhoneInputFormatter (+256 is fixed beside the field)', () {
    String typed(String text) => const UgPhoneInputFormatter()
        .formatEditUpdate(TextEditingValue.empty, TextEditingValue(text: text))
        .text;

    test(
      'groups nine digits 3-3-3',
      () => expect(typed('772123456'), '772 123 456'),
    );
    test('drops a leading 0', () => expect(typed('0772123456'), '772 123 456'));
    test(
      'drops a pasted +256',
      () => expect(typed('+256 772 123 456'), '772 123 456'),
    );
    test('drops a pasted 256 and 00256', () {
      expect(typed('256772123456'), '772 123 456');
      expect(typed('00256772123456'), '772 123 456');
    });
    test(
      'never more than nine digits',
      () => expect(typed('77212345678901'), '772 123 456'),
    );
    test(
      'ignores letters and symbols',
      () => expect(typed('7a7-2*1'), '772 1'),
    );
    test('partial input is grouped as typed', () {
      expect(typed('7'), '7');
      expect(typed('7721'), '772 1');
    });
    test('cursor stays at the end', () {
      final v = const UgPhoneInputFormatter().formatEditUpdate(
        TextEditingValue.empty,
        const TextEditingValue(text: '0772123456'),
      );
      expect(v.selection.baseOffset, v.text.length);
    });
  });
}
