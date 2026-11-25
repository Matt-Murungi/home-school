// ignore: unused_import
import 'package:intl/intl.dart' as intl;

import 'hs_strings.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class HsStringsEn extends HsStrings {
  HsStringsEn([String locale = 'en']) : super(locale);

  @override
  String get appName => 'Home School';

  @override
  String get welcomeTitle => 'Welcome to Home School';

  @override
  String get welcomeParentBody =>
      'Find a trusted teacher and follow your child\'s progress.';

  @override
  String get welcomeTeacherBody =>
      'Teach children near you and track your sessions and earnings.';

  @override
  String get getStarted => 'Get started';

  @override
  String environmentLabel(String name) {
    return 'Environment: $name';
  }

  @override
  String placeholderTabBody(String tab) {
    return '$tab will appear here.';
  }

  @override
  String get tabHome => 'Home';

  @override
  String get tabSessions => 'Sessions';

  @override
  String get tabProgress => 'Progress';

  @override
  String get tabBilling => 'Billing';

  @override
  String get tabAccount => 'Account';

  @override
  String get tabToday => 'Today';

  @override
  String get tabSchedule => 'Schedule';

  @override
  String get tabEarnings => 'Earnings';

  @override
  String get tabGrowth => 'Growth';

  @override
  String get retry => 'Try again';

  @override
  String get errorGeneric => 'Something went wrong. Please try again.';

  @override
  String offlineQueued(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count actions waiting to sync',
      one: '1 action waiting to sync',
      zero: 'Nothing waiting to sync',
    );
    return '$_temp0';
  }
}
