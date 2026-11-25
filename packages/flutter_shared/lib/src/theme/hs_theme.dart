import 'package:flutter/material.dart';

import 'tokens.dart';

/// Light theme built only from [HsColors], [HsRadius], [HsSpace], [HsFontSize].
/// Font family is the platform default until a brand font is chosen.
ThemeData buildHsTheme() {
  final scheme =
      ColorScheme.fromSeed(
        seedColor: HsColors.primary,
        brightness: Brightness.light,
      ).copyWith(
        primary: HsColors.primary,
        onPrimary: Colors.white,
        primaryContainer: HsColors.primarySoft,
        onPrimaryContainer: HsColors.text,
        secondary: HsColors.accent,
        error: HsColors.danger,
        surface: HsColors.surface,
        onSurface: HsColors.text,
        onSurfaceVariant: HsColors.textMuted,
        outline: HsColors.border,
      );
  const tapTarget = Size(HsSize.minTapTarget, HsSize.minTapTarget);
  final buttonShape = RoundedRectangleBorder(
    borderRadius: BorderRadius.circular(HsRadius.button),
  );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: HsColors.background,
    dividerColor: HsColors.border,
    textTheme: const TextTheme(
      bodySmall: TextStyle(
        fontSize: HsFontSize.caption,
        color: HsColors.textMuted,
      ),
      bodyMedium: TextStyle(fontSize: HsFontSize.small, color: HsColors.text),
      bodyLarge: TextStyle(fontSize: HsFontSize.body, color: HsColors.text),
      titleMedium: TextStyle(
        fontSize: HsFontSize.title,
        fontWeight: FontWeight.w600,
        color: HsColors.text,
      ),
      titleLarge: TextStyle(
        fontSize: HsFontSize.heading,
        fontWeight: FontWeight.w600,
        color: HsColors.text,
      ),
      headlineMedium: TextStyle(
        fontSize: HsFontSize.display,
        fontWeight: FontWeight.w700,
        color: HsColors.text,
      ),
    ),
    cardTheme: CardThemeData(
      color: HsColors.surface,
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(HsRadius.card),
        side: const BorderSide(color: HsColors.border),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(minimumSize: tapTarget, shape: buttonShape),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: tapTarget,
        shape: buttonShape,
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(minimumSize: tapTarget, shape: buttonShape),
    ),
    navigationBarTheme: const NavigationBarThemeData(
      backgroundColor: HsColors.surface,
      indicatorColor: HsColors.primarySoft,
    ),
  );
}
