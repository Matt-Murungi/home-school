import 'package:flutter/painting.dart';

/// Design tokens from Design Brief section 6 ("Starter tokens"). Defined once
/// here; apps must not hard-code colours, radii, spacing or font sizes.
abstract final class HsColors {
  static const primary = Color(0xFF0F6E6E);
  static const primarySoft = Color(0xFFE3F2F1);
  static const accent = Color(0xFFF2A93B);
  static const success = Color(0xFF2E8B57);
  static const warning = Color(0xFFD98E04);
  static const danger = Color(0xFFC0392B);
  static const info = Color(0xFF2D6CDF);
  static const text = Color(0xFF1B1F23);
  static const textMuted = Color(0xFF5F6B76);
  static const surface = Color(0xFFFFFFFF);
  static const background = Color(0xFFF6F7F8);
  static const border = Color(0xFFE1E4E8);
}

abstract final class HsRadius {
  static const double card = 12;
  static const double button = 10;
}

abstract final class HsSpace {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double xxl = 32;
  static const scale = <double>[xs, sm, md, lg, xl, xxl];
}

abstract final class HsFontSize {
  static const double caption = 12;
  static const double small = 14;
  static const double body = 16;
  static const double title = 18;
  static const double heading = 22;
  static const double display = 28;
  static const scale = <double>[caption, small, body, title, heading, display];
}

abstract final class HsSize {
  /// Design Brief section 10: minimum tap target 48 x 48 dp.
  static const double minTapTarget = 48;
}
