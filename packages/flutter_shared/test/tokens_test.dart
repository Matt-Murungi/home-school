import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

void main() {
  test('colour tokens match Design Brief section 6', () {
    expect(HsColors.primary, const Color(0xFF0F6E6E));
    expect(HsColors.primarySoft, const Color(0xFFE3F2F1));
    expect(HsColors.accent, const Color(0xFFF2A93B));
    expect(HsColors.success, const Color(0xFF2E8B57));
    expect(HsColors.warning, const Color(0xFFD98E04));
    expect(HsColors.danger, const Color(0xFFC0392B));
    expect(HsColors.info, const Color(0xFF2D6CDF));
    expect(HsColors.text, const Color(0xFF1B1F23));
    expect(HsColors.textMuted, const Color(0xFF5F6B76));
    expect(HsColors.surface, const Color(0xFFFFFFFF));
    expect(HsColors.background, const Color(0xFFF6F7F8));
    expect(HsColors.border, const Color(0xFFE1E4E8));
  });

  test('radii, spacing and type scale match the brief', () {
    expect(HsRadius.card, 12);
    expect(HsRadius.button, 10);
    expect(HsSpace.scale, [4, 8, 12, 16, 24, 32]);
    expect(HsFontSize.scale, [12, 14, 16, 18, 22, 28]);
    expect(HsFontSize.body, 16);
    expect(HsSize.minTapTarget, 48);
  });

  test('body text and muted text meet 4.5:1 contrast on background', () {
    double lum(Color c) => c.computeLuminance();
    double ratio(Color a, Color b) {
      final l1 = lum(a), l2 = lum(b);
      final hi = l1 > l2 ? l1 : l2, lo = l1 > l2 ? l2 : l1;
      return (hi + 0.05) / (lo + 0.05);
    }

    expect(ratio(HsColors.text, HsColors.background), greaterThan(4.5));
    expect(ratio(HsColors.textMuted, HsColors.background), greaterThan(4.5));
    expect(ratio(Colors.white, HsColors.primary), greaterThan(4.5));
  });

  test('theme applies tokens', () {
    final t = buildHsTheme();
    expect(t.colorScheme.primary, HsColors.primary);
    expect(t.scaffoldBackgroundColor, HsColors.background);
    expect(t.textTheme.bodyLarge!.fontSize, HsFontSize.body);
    expect(t.textTheme.headlineMedium!.fontSize, 28);
    expect(
      t.filledButtonTheme.style!.minimumSize!.resolve({}),
      const Size(48, 48),
    );
  });
}
