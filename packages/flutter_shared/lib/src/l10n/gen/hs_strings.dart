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
