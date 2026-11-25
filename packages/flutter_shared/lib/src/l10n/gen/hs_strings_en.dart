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

  @override
  String get signInExisting => 'I already have an account';

  @override
  String get otpPhoneTitle => 'Enter your phone number';

  @override
  String get otpPhoneNote => 'We\'ll send a code by SMS.';

  @override
  String get otpCountryCode => '+256';

  @override
  String get otpPhoneLabel => 'Phone number';

  @override
  String get otpPhoneHint => '772 123 456';

  @override
  String get otpPhoneFieldSemantics =>
      'Phone number, country code plus 256 fixed. Enter the nine digits after the country code.';

  @override
  String get otpSendCode => 'Send code';

  @override
  String get otpSending => 'Sending code';

  @override
  String get otpInvalidPhone =>
      'Enter a valid Ugandan mobile number, for example 0772 123 456.';

  @override
  String otpRateLimited(String wait) {
    return 'Too many codes requested. Try again in $wait.';
  }

  @override
  String otpLocked(String wait) {
    return 'Too many wrong codes. For your safety, try again in $wait.';
  }

  @override
  String get otpOffline =>
      'You\'re offline. Check your connection and try again.';

  @override
  String get otpSmsUnavailable =>
      'We couldn\'t send the SMS right now. Please try again in a moment.';

  @override
  String get otpNumberUnreachable =>
      'We can\'t send an SMS to this number. Use a different number.';

  @override
  String get otpAccountBlocked =>
      'This account can\'t sign in. Please contact support.';

  @override
  String get otpServerUnavailable =>
      'Sign-in is temporarily unavailable. Please try again.';

  @override
  String get otpUnknownError => 'Something went wrong. Please try again.';

  @override
  String otpDurationSeconds(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count seconds',
      one: '1 second',
    );
    return '$_temp0';
  }

  @override
  String otpDurationMinutes(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count minutes',
      one: '1 minute',
    );
    return '$_temp0';
  }

  @override
  String get otpCodeTitle => 'Enter the 6-digit code';

  @override
  String otpCodeSentTo(String phone) {
    return 'We sent a code to $phone';
  }

  @override
  String get otpCodeFieldSemantics => 'Verification code, 6 digits';

  @override
  String get otpVerify => 'Verify';

  @override
  String get otpVerifying => 'Verifying code';

  @override
  String otpWrongCode(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count tries left',
      one: '1 try left',
      zero: 'No tries left',
    );
    return 'That code isn\'t right. $_temp0.';
  }

  @override
  String get otpWrongCodeNoCount =>
      'That code isn\'t right. Check it and try again.';

  @override
  String get otpExpired => 'That code has expired. Request a new one.';

  @override
  String get otpTooManyAttempts => 'Too many wrong tries. Request a new code.';

  @override
  String otpResendIn(int seconds) {
    return 'Resend code in ${seconds}s';
  }

  @override
  String get otpResend => 'Resend code';

  @override
  String get otpChangeNumber => 'Use a different number';

  @override
  String get otpSmsDelayed =>
      'Still no SMS? It can take a couple of minutes on busy networks. You can resend the code or use a different number.';

  @override
  String get otpHelp => 'Need help?';

  @override
  String get otpHelpTitle => 'No SMS arriving?';

  @override
  String get otpHelpBody =>
      'Check that your phone has signal and that the number is right. SMS can be delayed on busy networks. If nothing arrives after you resend the code, use a different number or contact support.';

  @override
  String get otpClose => 'Close';
}
