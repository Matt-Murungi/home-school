import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'hs_strings_en.dart';

// ignore_for_file: type=lint

/// Callers can lookup localized strings with an instance of HsStrings
/// returned by `HsStrings.of(context)`.
///
/// Applications need to include `HsStrings.delegate()` in their app's
/// `localizationDelegates` list, and the locales they support in the app's
/// `supportedLocales` list. For example:
///
/// ```dart
/// import 'gen/hs_strings.dart';
///
/// return MaterialApp(
///   localizationsDelegates: HsStrings.localizationsDelegates,
///   supportedLocales: HsStrings.supportedLocales,
///   home: MyApplicationHome(),
/// );
/// ```
///
/// ## Update pubspec.yaml
///
/// Please make sure to update your pubspec.yaml to include the following
/// packages:
///
/// ```yaml
/// dependencies:
///   # Internationalization support.
///   flutter_localizations:
///     sdk: flutter
///   intl: any # Use the pinned version from flutter_localizations
///
///   # Rest of dependencies
/// ```
///
/// ## iOS Applications
///
/// iOS applications define key application metadata, including supported
/// locales, in an Info.plist file that is built into the application bundle.
/// To configure the locales supported by your app, you’ll need to edit this
/// file.
///
/// First, open your project’s ios/Runner.xcworkspace Xcode workspace file.
/// Then, in the Project Navigator, open the Info.plist file under the Runner
/// project’s Runner folder.
///
/// Next, select the Information Property List item, select Add Item from the
/// Editor menu, then select Localizations from the pop-up menu.
///
/// Select and expand the newly-created Localizations item then, for each
/// locale your application supports, add a new item and select the locale
/// you wish to add from the pop-up menu in the Value field. This list should
/// be consistent with the languages listed in the HsStrings.supportedLocales
/// property.
abstract class HsStrings {
  HsStrings(String locale)
    : localeName = intl.Intl.canonicalizedLocale(locale.toString());

  final String localeName;

  static HsStrings of(BuildContext context) {
    return Localizations.of<HsStrings>(context, HsStrings)!;
  }

  static const LocalizationsDelegate<HsStrings> delegate = _HsStringsDelegate();

  /// A list of this localizations delegate along with the default localizations
  /// delegates.
  ///
  /// Returns a list of localizations delegates containing this delegate along with
  /// GlobalMaterialLocalizations.delegate, GlobalCupertinoLocalizations.delegate,
  /// and GlobalWidgetsLocalizations.delegate.
  ///
  /// Additional delegates can be added by appending to this list in
  /// MaterialApp. This list does not have to be used at all if a custom list
  /// of delegates is preferred or required.
  static const List<LocalizationsDelegate<dynamic>> localizationsDelegates =
      <LocalizationsDelegate<dynamic>>[
        delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
      ];

  /// A list of this localizations delegate's supported locales.
  static const List<Locale> supportedLocales = <Locale>[Locale('en')];

  /// Working name until a brand exists (Design Brief section 6).
  ///
  /// In en, this message translates to:
  /// **'Home School'**
  String get appName;

  /// No description provided for @welcomeTitle.
  ///
  /// In en, this message translates to:
  /// **'Welcome to Home School'**
  String get welcomeTitle;

  /// No description provided for @welcomeParentBody.
  ///
  /// In en, this message translates to:
  /// **'Find a trusted teacher and follow your child\'s progress.'**
  String get welcomeParentBody;

  /// No description provided for @welcomeTeacherBody.
  ///
  /// In en, this message translates to:
  /// **'Teach children near you and track your sessions and earnings.'**
  String get welcomeTeacherBody;

  /// No description provided for @getStarted.
  ///
  /// In en, this message translates to:
  /// **'Get started'**
  String get getStarted;

  /// No description provided for @environmentLabel.
  ///
  /// In en, this message translates to:
  /// **'Environment: {name}'**
  String environmentLabel(String name);

  /// No description provided for @placeholderTabBody.
  ///
  /// In en, this message translates to:
  /// **'{tab} will appear here.'**
  String placeholderTabBody(String tab);

  /// No description provided for @tabHome.
  ///
  /// In en, this message translates to:
  /// **'Home'**
  String get tabHome;

  /// No description provided for @tabSessions.
  ///
  /// In en, this message translates to:
  /// **'Sessions'**
  String get tabSessions;

  /// No description provided for @tabProgress.
  ///
  /// In en, this message translates to:
  /// **'Progress'**
  String get tabProgress;

  /// No description provided for @tabBilling.
  ///
  /// In en, this message translates to:
  /// **'Billing'**
  String get tabBilling;

  /// No description provided for @tabAccount.
  ///
  /// In en, this message translates to:
  /// **'Account'**
  String get tabAccount;

  /// No description provided for @tabToday.
  ///
  /// In en, this message translates to:
  /// **'Today'**
  String get tabToday;

  /// No description provided for @tabSchedule.
  ///
  /// In en, this message translates to:
  /// **'Schedule'**
  String get tabSchedule;

  /// No description provided for @tabEarnings.
  ///
  /// In en, this message translates to:
  /// **'Earnings'**
  String get tabEarnings;

  /// No description provided for @tabGrowth.
  ///
  /// In en, this message translates to:
  /// **'Growth'**
  String get tabGrowth;

  /// No description provided for @retry.
  ///
  /// In en, this message translates to:
  /// **'Try again'**
  String get retry;

  /// No description provided for @errorGeneric.
  ///
  /// In en, this message translates to:
  /// **'Something went wrong. Please try again.'**
  String get errorGeneric;

  /// No description provided for @offlineQueued.
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =0{Nothing waiting to sync} =1{1 action waiting to sync} other{{count} actions waiting to sync}}'**
  String offlineQueued(int count);

  /// No description provided for @signInExisting.
  ///
  /// In en, this message translates to:
  /// **'I already have an account'**
  String get signInExisting;

  /// No description provided for @otpPhoneTitle.
  ///
  /// In en, this message translates to:
  /// **'Enter your phone number'**
  String get otpPhoneTitle;

  /// No description provided for @otpPhoneNote.
  ///
  /// In en, this message translates to:
  /// **'We\'ll send a code by SMS.'**
  String get otpPhoneNote;

  /// Fixed Uganda country code shown beside the number field.
  ///
  /// In en, this message translates to:
  /// **'+256'**
  String get otpCountryCode;

  /// No description provided for @otpPhoneLabel.
  ///
  /// In en, this message translates to:
  /// **'Phone number'**
  String get otpPhoneLabel;

  /// No description provided for @otpPhoneHint.
  ///
  /// In en, this message translates to:
  /// **'772 123 456'**
  String get otpPhoneHint;

  /// No description provided for @otpPhoneFieldSemantics.
  ///
  /// In en, this message translates to:
  /// **'Phone number, country code plus 256 fixed. Enter the nine digits after the country code.'**
  String get otpPhoneFieldSemantics;

  /// No description provided for @otpSendCode.
  ///
  /// In en, this message translates to:
  /// **'Send code'**
  String get otpSendCode;

  /// No description provided for @otpSending.
  ///
  /// In en, this message translates to:
  /// **'Sending code'**
  String get otpSending;

  /// No description provided for @otpInvalidPhone.
  ///
  /// In en, this message translates to:
  /// **'Enter a valid Ugandan mobile number, for example 0772 123 456.'**
  String get otpInvalidPhone;

  /// No description provided for @otpRateLimited.
  ///
  /// In en, this message translates to:
  /// **'Too many codes requested. Try again in {wait}.'**
  String otpRateLimited(String wait);

  /// No description provided for @otpLocked.
  ///
  /// In en, this message translates to:
  /// **'Too many wrong codes. For your safety, try again in {wait}.'**
  String otpLocked(String wait);

  /// No description provided for @otpOffline.
  ///
  /// In en, this message translates to:
  /// **'You\'re offline. Check your connection and try again.'**
  String get otpOffline;

  /// No description provided for @otpSmsUnavailable.
  ///
  /// In en, this message translates to:
  /// **'We couldn\'t send the SMS right now. Please try again in a moment.'**
  String get otpSmsUnavailable;

  /// No description provided for @otpNumberUnreachable.
  ///
  /// In en, this message translates to:
  /// **'We can\'t send an SMS to this number. Use a different number.'**
  String get otpNumberUnreachable;

  /// No description provided for @otpAccountBlocked.
  ///
  /// In en, this message translates to:
  /// **'This account can\'t sign in. Please contact support.'**
  String get otpAccountBlocked;

  /// No description provided for @otpServerUnavailable.
  ///
  /// In en, this message translates to:
  /// **'Sign-in is temporarily unavailable. Please try again.'**
  String get otpServerUnavailable;

  /// No description provided for @otpUnknownError.
  ///
  /// In en, this message translates to:
  /// **'Something went wrong. Please try again.'**
  String get otpUnknownError;

  /// No description provided for @otpDurationSeconds.
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 second} other{{count} seconds}}'**
  String otpDurationSeconds(int count);

  /// No description provided for @otpDurationMinutes.
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 minute} other{{count} minutes}}'**
  String otpDurationMinutes(int count);

  /// No description provided for @otpCodeTitle.
  ///
  /// In en, this message translates to:
  /// **'Enter the 6-digit code'**
  String get otpCodeTitle;

  /// No description provided for @otpCodeSentTo.
  ///
  /// In en, this message translates to:
  /// **'We sent a code to {phone}'**
  String otpCodeSentTo(String phone);

  /// No description provided for @otpCodeFieldSemantics.
  ///
  /// In en, this message translates to:
  /// **'Verification code, 6 digits'**
  String get otpCodeFieldSemantics;

  /// No description provided for @otpVerify.
  ///
  /// In en, this message translates to:
  /// **'Verify'**
  String get otpVerify;

  /// No description provided for @otpVerifying.
  ///
  /// In en, this message translates to:
  /// **'Verifying code'**
  String get otpVerifying;

  /// No description provided for @otpWrongCode.
  ///
  /// In en, this message translates to:
  /// **'That code isn\'t right. {count, plural, =0{No tries left} =1{1 try left} other{{count} tries left}}.'**
  String otpWrongCode(int count);

  /// No description provided for @otpWrongCodeNoCount.
  ///
  /// In en, this message translates to:
  /// **'That code isn\'t right. Check it and try again.'**
  String get otpWrongCodeNoCount;

  /// No description provided for @otpExpired.
  ///
  /// In en, this message translates to:
  /// **'That code has expired. Request a new one.'**
  String get otpExpired;

  /// No description provided for @otpTooManyAttempts.
  ///
  /// In en, this message translates to:
  /// **'Too many wrong tries. Request a new code.'**
  String get otpTooManyAttempts;

  /// No description provided for @otpResendIn.
  ///
  /// In en, this message translates to:
  /// **'Resend code in {seconds}s'**
  String otpResendIn(int seconds);

  /// No description provided for @otpResend.
  ///
  /// In en, this message translates to:
  /// **'Resend code'**
  String get otpResend;

  /// No description provided for @otpChangeNumber.
  ///
  /// In en, this message translates to:
  /// **'Use a different number'**
  String get otpChangeNumber;

  /// No description provided for @otpSmsDelayed.
  ///
  /// In en, this message translates to:
  /// **'Still no SMS? It can take a couple of minutes on busy networks. You can resend the code or use a different number.'**
  String get otpSmsDelayed;

  /// No description provided for @otpHelp.
  ///
  /// In en, this message translates to:
  /// **'Need help?'**
  String get otpHelp;

  /// No description provided for @otpHelpTitle.
  ///
  /// In en, this message translates to:
  /// **'No SMS arriving?'**
  String get otpHelpTitle;

  /// No description provided for @otpHelpBody.
  ///
  /// In en, this message translates to:
  /// **'Check that your phone has signal and that the number is right. SMS can be delayed on busy networks. If nothing arrives after you resend the code, use a different number or contact support.'**
  String get otpHelpBody;

  /// No description provided for @otpClose.
  ///
  /// In en, this message translates to:
  /// **'Close'**
  String get otpClose;

  /// No description provided for @startLoading.
  ///
  /// In en, this message translates to:
  /// **'Loading'**
  String get startLoading;

  /// No description provided for @wrongAppTitleParent.
  ///
  /// In en, this message translates to:
  /// **'This is the parent app'**
  String get wrongAppTitleParent;

  /// No description provided for @wrongAppTitleTeacher.
  ///
  /// In en, this message translates to:
  /// **'This is the teacher app'**
  String get wrongAppTitleTeacher;

  /// No description provided for @wrongAppToParent.
  ///
  /// In en, this message translates to:
  /// **'Your account is a parent account. Please open the Home School parent app instead.'**
  String get wrongAppToParent;

  /// No description provided for @wrongAppToTeacher.
  ///
  /// In en, this message translates to:
  /// **'Your account is a teacher account. Please open the Home School teacher app instead.'**
  String get wrongAppToTeacher;

  /// No description provided for @wrongAppToAdmin.
  ///
  /// In en, this message translates to:
  /// **'Your account is a staff account. Staff sign in to the admin dashboard on the web, not in this app.'**
  String get wrongAppToAdmin;

  /// No description provided for @noAccessTitle.
  ///
  /// In en, this message translates to:
  /// **'No access with this account'**
  String get noAccessTitle;

  /// No description provided for @noAccessBody.
  ///
  /// In en, this message translates to:
  /// **'This account can\'t use this app. Please contact support if you think this is a mistake.'**
  String get noAccessBody;

  /// No description provided for @signOut.
  ///
  /// In en, this message translates to:
  /// **'Sign out'**
  String get signOut;

  /// No description provided for @suspendedTitle.
  ///
  /// In en, this message translates to:
  /// **'Your account is suspended'**
  String get suspendedTitle;

  /// No description provided for @suspendedBody.
  ///
  /// In en, this message translates to:
  /// **'You can\'t use the app while your account is suspended. Please contact support to find out more.'**
  String get suspendedBody;

  /// No description provided for @suspendedBack.
  ///
  /// In en, this message translates to:
  /// **'Back'**
  String get suspendedBack;

  /// No description provided for @otpStaffAccount.
  ///
  /// In en, this message translates to:
  /// **'Staff accounts sign in to the admin dashboard on the web, not in this app.'**
  String get otpStaffAccount;

  /// No description provided for @otpRoleRevoked.
  ///
  /// In en, this message translates to:
  /// **'This account can\'t use this app. Please contact support.'**
  String get otpRoleRevoked;
}

class _HsStringsDelegate extends LocalizationsDelegate<HsStrings> {
  const _HsStringsDelegate();

  @override
  Future<HsStrings> load(Locale locale) {
    return SynchronousFuture<HsStrings>(lookupHsStrings(locale));
  }

  @override
  bool isSupported(Locale locale) =>
      <String>['en'].contains(locale.languageCode);

  @override
  bool shouldReload(_HsStringsDelegate old) => false;
}

HsStrings lookupHsStrings(Locale locale) {
  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'en':
      return HsStringsEn();
  }

  throw FlutterError(
    'HsStrings.delegate failed to load unsupported locale "$locale". This is likely '
    'an issue with the localizations generation tool. Please file an issue '
    'on GitHub with a reproducible sample app and the gen-l10n configuration '
    'that was used.',
  );
}
