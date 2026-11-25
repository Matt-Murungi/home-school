import 'package:flutter/services.dart';

/// Ugandan mobile numbers (A01, ACC-1). Mirrors the core service's
/// `normalize_ug_phone`; both are tested against
/// `test/fixtures/ug_phone_cases.json`.
const ugCountryCode = '256';

final _allowed = RegExp(r'^\+?[0-9 ().\-]+$');
final _national = RegExp(r'^7[0-9]{8}$');

/// Returns the E.164 number (`+256772123456`) or null when [raw] is not a
/// Ugandan mobile number. Accepts `0772 123 456`, `772123456`,
/// `256772123456`, `+256 772-123-456` and `00256...`.
String? normalizeUgPhone(String raw) {
  if (raw.length > 32) return null;
  final s = raw.trim();
  if (s.isEmpty || !_allowed.hasMatch(s) || s.indexOf('+', 1) != -1) {
    return null;
  }
  final digits = s.replaceAll(RegExp(r'[^0-9]'), '');
  final String? national;
  if (s.startsWith('+')) {
    national = digits.startsWith(ugCountryCode) && digits.length == 12
        ? digits.substring(3)
        : null;
  } else if (digits.startsWith('00$ugCountryCode') && digits.length == 14) {
    national = digits.substring(5);
  } else if (digits.startsWith(ugCountryCode) && digits.length == 12) {
    national = digits.substring(3);
  } else if (digits.startsWith('0') && digits.length == 10) {
    national = digits.substring(1);
  } else if (digits.length == 9) {
    national = digits;
  } else {
    national = null;
  }
  if (national == null || !_national.hasMatch(national)) return null;
  return '+$ugCountryCode$national';
}

/// `+256772123456` -> `+256 *** *** 456`: what the code screen shows.
String maskPhone(String e164) {
  final tail = e164.length >= 3 ? e164.substring(e164.length - 3) : e164;
  return '+$ugCountryCode *** *** $tail';
}

/// Keeps only what belongs in the national-number field: the user types or
/// pastes `0772...`, `+256772...` or `772...`; the field shows `772 123 456`
/// (the +256 prefix is fixed next to it). At most nine digits.
class UgPhoneInputFormatter extends TextInputFormatter {
  const UgPhoneInputFormatter();

  /// The national digits of [text]: leading `+256`, `256`, `00256` or `0` removed.
  static String nationalDigits(String text) {
    var d = text.replaceAll(RegExp(r'[^0-9]'), '');
    if (text.trimLeft().startsWith('+') && d.startsWith(ugCountryCode)) {
      d = d.substring(3);
    } else if (d.startsWith('00$ugCountryCode')) {
      d = d.substring(5);
    } else if (d.length > 9 && d.startsWith(ugCountryCode)) {
      d = d.substring(3);
    } else if (d.startsWith('0')) {
      d = d.substring(1);
    }
    return d.length > 9 ? d.substring(0, 9) : d;
  }

  static String group(String d) {
    final b = StringBuffer();
    for (var i = 0; i < d.length; i++) {
      if (i == 3 || i == 6) b.write(' ');
      b.write(d[i]);
    }
    return b.toString();
  }

  @override
  TextEditingValue formatEditUpdate(
    TextEditingValue oldValue,
    TextEditingValue newValue,
  ) {
    final text = group(nationalDigits(newValue.text));
    return TextEditingValue(
      text: text,
      selection: TextSelection.collapsed(offset: text.length),
    );
  }
}
