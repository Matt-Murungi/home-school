import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'teacher_strings_en.dart';

// ignore_for_file: type=lint

/// Callers can lookup localized strings with an instance of TeacherStrings
/// returned by `TeacherStrings.of(context)`.
///
/// Applications need to include `TeacherStrings.delegate()` in their app's
/// `localizationDelegates` list, and the locales they support in the app's
/// `supportedLocales` list. For example:
///
/// ```dart
/// import 'gen/teacher_strings.dart';
///
/// return MaterialApp(
///   localizationsDelegates: TeacherStrings.localizationsDelegates,
///   supportedLocales: TeacherStrings.supportedLocales,
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
/// be consistent with the languages listed in the TeacherStrings.supportedLocales
/// property.
abstract class TeacherStrings {
  TeacherStrings(String locale)
    : localeName = intl.Intl.canonicalizedLocale(locale.toString());

  final String localeName;

  static TeacherStrings of(BuildContext context) {
    return Localizations.of<TeacherStrings>(context, TeacherStrings)!;
  }

  static const LocalizationsDelegate<TeacherStrings> delegate =
      _TeacherStringsDelegate();

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

  /// No description provided for @applyTitle.
  ///
  /// In en, this message translates to:
  /// **'Apply to teach'**
  String get applyTitle;

  /// No description provided for @loading.
  ///
  /// In en, this message translates to:
  /// **'Loading'**
  String get loading;

  /// No description provided for @loadFailed.
  ///
  /// In en, this message translates to:
  /// **'We could not load your application and there is no copy on this phone yet. Check your connection and try again.'**
  String get loadFailed;

  /// No description provided for @tryAgain.
  ///
  /// In en, this message translates to:
  /// **'Try again'**
  String get tryAgain;

  /// No description provided for @back.
  ///
  /// In en, this message translates to:
  /// **'Back'**
  String get back;

  /// No description provided for @next.
  ///
  /// In en, this message translates to:
  /// **'Next'**
  String get next;

  /// No description provided for @edit.
  ///
  /// In en, this message translates to:
  /// **'Edit'**
  String get edit;

  /// No description provided for @goHome.
  ///
  /// In en, this message translates to:
  /// **'Go to the app'**
  String get goHome;

  /// No description provided for @stepOf.
  ///
  /// In en, this message translates to:
  /// **'Step {step} of {total}'**
  String stepOf(int step, int total);

  /// No description provided for @progressSemantic.
  ///
  /// In en, this message translates to:
  /// **'Step {step} of {total}: {title}'**
  String progressSemantic(int step, int total, String title);

  /// No description provided for @stepPersonal.
  ///
  /// In en, this message translates to:
  /// **'Personal details'**
  String get stepPersonal;

  /// No description provided for @stepQualifications.
  ///
  /// In en, this message translates to:
  /// **'Qualifications and curricula'**
  String get stepQualifications;

  /// No description provided for @stepDocuments.
  ///
  /// In en, this message translates to:
  /// **'Documents'**
  String get stepDocuments;

  /// No description provided for @stepReferees.
  ///
  /// In en, this message translates to:
  /// **'Referees'**
  String get stepReferees;

  /// No description provided for @stepZones.
  ///
  /// In en, this message translates to:
  /// **'Zones and availability'**
  String get stepZones;

  /// No description provided for @stepReview.
  ///
  /// In en, this message translates to:
  /// **'Review and submit'**
  String get stepReview;

  /// No description provided for @syncSaved.
  ///
  /// In en, this message translates to:
  /// **'Saved. You can leave and come back later.'**
  String get syncSaved;

  /// No description provided for @syncSaving.
  ///
  /// In en, this message translates to:
  /// **'Saving...'**
  String get syncSaving;

  /// No description provided for @syncOffline.
  ///
  /// In en, this message translates to:
  /// **'Saved on this phone. We will send it when you are online.'**
  String get syncOffline;

  /// No description provided for @syncRateLimited.
  ///
  /// In en, this message translates to:
  /// **'Saved on this phone. Too many saves; we will retry shortly.'**
  String get syncRateLimited;

  /// No description provided for @syncConflict.
  ///
  /// In en, this message translates to:
  /// **'This application was changed somewhere else. Choose which version to keep.'**
  String get syncConflict;

  /// No description provided for @syncLocked.
  ///
  /// In en, this message translates to:
  /// **'This application can no longer be edited.'**
  String get syncLocked;

  /// No description provided for @conflictTitle.
  ///
  /// In en, this message translates to:
  /// **'Two versions of your application'**
  String get conflictTitle;

  /// No description provided for @conflictBody.
  ///
  /// In en, this message translates to:
  /// **'You made changes on this phone while the saved version changed elsewhere. Keep the version on this phone, or use the saved one.'**
  String get conflictBody;

  /// No description provided for @conflictKeepMine.
  ///
  /// In en, this message translates to:
  /// **'Keep this phone\'s version'**
  String get conflictKeepMine;

  /// No description provided for @conflictUseTheirs.
  ///
  /// In en, this message translates to:
  /// **'Use the saved version'**
  String get conflictUseTheirs;

  /// No description provided for @fieldFullName.
  ///
  /// In en, this message translates to:
  /// **'Full name'**
  String get fieldFullName;

  /// No description provided for @fieldTitle.
  ///
  /// In en, this message translates to:
  /// **'Preferred title'**
  String get fieldTitle;

  /// No description provided for @titleMr.
  ///
  /// In en, this message translates to:
  /// **'Mr'**
  String get titleMr;

  /// No description provided for @titleMs.
  ///
  /// In en, this message translates to:
  /// **'Ms'**
  String get titleMs;

  /// No description provided for @titleTeacher.
  ///
  /// In en, this message translates to:
  /// **'Teacher'**
  String get titleTeacher;

  /// No description provided for @fieldGender.
  ///
  /// In en, this message translates to:
  /// **'Gender'**
  String get fieldGender;

  /// No description provided for @genderFemale.
  ///
  /// In en, this message translates to:
  /// **'Female'**
  String get genderFemale;

  /// No description provided for @genderMale.
  ///
  /// In en, this message translates to:
  /// **'Male'**
  String get genderMale;

  /// No description provided for @fieldDob.
  ///
  /// In en, this message translates to:
  /// **'Date of birth'**
  String get fieldDob;

  /// No description provided for @dobHelp.
  ///
  /// In en, this message translates to:
  /// **'You must be at least 18. Only our team sees this.'**
  String get dobHelp;

  /// No description provided for @dobPlaceholder.
  ///
  /// In en, this message translates to:
  /// **'Choose a date'**
  String get dobPlaceholder;

  /// No description provided for @fieldLanguages.
  ///
  /// In en, this message translates to:
  /// **'Languages you speak'**
  String get fieldLanguages;

  /// No description provided for @langEnglish.
  ///
  /// In en, this message translates to:
  /// **'English'**
  String get langEnglish;

  /// No description provided for @langLuganda.
  ///
  /// In en, this message translates to:
  /// **'Luganda'**
  String get langLuganda;

  /// No description provided for @langSwahili.
  ///
  /// In en, this message translates to:
  /// **'Swahili'**
  String get langSwahili;

  /// No description provided for @langRunyankole.
  ///
  /// In en, this message translates to:
  /// **'Runyankole / Rukiga'**
  String get langRunyankole;

  /// No description provided for @langLusoga.
  ///
  /// In en, this message translates to:
  /// **'Lusoga'**
  String get langLusoga;

  /// No description provided for @langAteso.
  ///
  /// In en, this message translates to:
  /// **'Ateso'**
  String get langAteso;

  /// No description provided for @langAcholi.
  ///
  /// In en, this message translates to:
  /// **'Acholi'**
  String get langAcholi;

  /// No description provided for @langOther.
  ///
  /// In en, this message translates to:
  /// **'Other'**
  String get langOther;

  /// No description provided for @photoTitle.
  ///
  /// In en, this message translates to:
  /// **'Your photo'**
  String get photoTitle;

  /// No description provided for @photoGuidanceTitle.
  ///
  /// In en, this message translates to:
  /// **'A good photo'**
  String get photoGuidanceTitle;

  /// No description provided for @photoGuide1.
  ///
  /// In en, this message translates to:
  /// **'Plain, light background.'**
  String get photoGuide1;

  /// No description provided for @photoGuide2.
  ///
  /// In en, this message translates to:
  /// **'Shoulders up, facing the camera.'**
  String get photoGuide2;

  /// No description provided for @photoGuide3.
  ///
  /// In en, this message translates to:
  /// **'Good light on your face; no sunglasses or hat.'**
  String get photoGuide3;

  /// No description provided for @photoGuide4.
  ///
  /// In en, this message translates to:
  /// **'Just you in the photo.'**
  String get photoGuide4;

  /// No description provided for @photoTake.
  ///
  /// In en, this message translates to:
  /// **'Take a photo'**
  String get photoTake;

  /// No description provided for @photoChoose.
  ///
  /// In en, this message translates to:
  /// **'Choose from phone'**
  String get photoChoose;

  /// No description provided for @photoAdjust.
  ///
  /// In en, this message translates to:
  /// **'Adjust crop'**
  String get photoAdjust;

  /// No description provided for @photoRemove.
  ///
  /// In en, this message translates to:
  /// **'Remove photo'**
  String get photoRemove;

  /// No description provided for @photoPickFailed.
  ///
  /// In en, this message translates to:
  /// **'We could not open the camera or photos. Check the app\'s permissions and try again.'**
  String get photoPickFailed;

  /// No description provided for @photoPreviewLabel.
  ///
  /// In en, this message translates to:
  /// **'Your chosen photo'**
  String get photoPreviewLabel;

  /// No description provided for @photoAdded.
  ///
  /// In en, this message translates to:
  /// **'Photo added'**
  String get photoAdded;

  /// No description provided for @photoMissing.
  ///
  /// In en, this message translates to:
  /// **'Photo missing'**
  String get photoMissing;

  /// No description provided for @cropTitle.
  ///
  /// In en, this message translates to:
  /// **'Crop your photo'**
  String get cropTitle;

  /// No description provided for @cropHelp.
  ///
  /// In en, this message translates to:
  /// **'Zoom and move the square so your face and shoulders are in the middle.'**
  String get cropHelp;

  /// No description provided for @cropZoom.
  ///
  /// In en, this message translates to:
  /// **'Zoom'**
  String get cropZoom;

  /// No description provided for @cropLeftRight.
  ///
  /// In en, this message translates to:
  /// **'Move left or right'**
  String get cropLeftRight;

  /// No description provided for @cropUpDown.
  ///
  /// In en, this message translates to:
  /// **'Move up or down'**
  String get cropUpDown;

  /// No description provided for @cropDone.
  ///
  /// In en, this message translates to:
  /// **'Use this crop'**
  String get cropDone;

  /// No description provided for @fieldTeacherType.
  ///
  /// In en, this message translates to:
  /// **'I am a'**
  String get fieldTeacherType;

  /// No description provided for @typeRegistered.
  ///
  /// In en, this message translates to:
  /// **'Registered teacher'**
  String get typeRegistered;

  /// No description provided for @typeGraduate.
  ///
  /// In en, this message translates to:
  /// **'Graduate'**
  String get typeGraduate;

  /// No description provided for @fieldRegistration.
  ///
  /// In en, this message translates to:
  /// **'Teacher registration number'**
  String get fieldRegistration;

  /// No description provided for @fieldRegistrationOptional.
  ///
  /// In en, this message translates to:
  /// **'Registration number (optional)'**
  String get fieldRegistrationOptional;

  /// No description provided for @registrationGraduateHelp.
  ///
  /// In en, this message translates to:
  /// **'Graduates can leave this empty.'**
  String get registrationGraduateHelp;

  /// No description provided for @fieldHighestQualification.
  ///
  /// In en, this message translates to:
  /// **'Highest qualification'**
  String get fieldHighestQualification;

  /// No description provided for @qualCertificate.
  ///
  /// In en, this message translates to:
  /// **'Certificate'**
  String get qualCertificate;

  /// No description provided for @qualDiploma.
  ///
  /// In en, this message translates to:
  /// **'Diploma'**
  String get qualDiploma;

  /// No description provided for @qualDegree.
  ///
  /// In en, this message translates to:
  /// **'Degree'**
  String get qualDegree;

  /// No description provided for @qualPostgraduate.
  ///
  /// In en, this message translates to:
  /// **'Postgraduate diploma'**
  String get qualPostgraduate;

  /// No description provided for @qualMasters.
  ///
  /// In en, this message translates to:
  /// **'Master\'s degree'**
  String get qualMasters;

  /// No description provided for @qualDoctorate.
  ///
  /// In en, this message translates to:
  /// **'Doctorate'**
  String get qualDoctorate;

  /// No description provided for @fieldInstitution.
  ///
  /// In en, this message translates to:
  /// **'Institution'**
  String get fieldInstitution;

  /// No description provided for @fieldYearsTeaching.
  ///
  /// In en, this message translates to:
  /// **'Years of teaching'**
  String get fieldYearsTeaching;

  /// No description provided for @fieldCurrentSchool.
  ///
  /// In en, this message translates to:
  /// **'Current school (optional)'**
  String get fieldCurrentSchool;

  /// No description provided for @currentSchoolHelp.
  ///
  /// In en, this message translates to:
  /// **'Only our admin team sees this. It is never shown to families.'**
  String get currentSchoolHelp;

  /// No description provided for @curriculaTitle.
  ///
  /// In en, this message translates to:
  /// **'Curricula you want to teach'**
  String get curriculaTitle;

  /// No description provided for @curriculaHelp.
  ///
  /// In en, this message translates to:
  /// **'Pick each curriculum, then the levels and subjects you can teach. Our team approves them after review.'**
  String get curriculaHelp;

  /// No description provided for @levelsTitle.
  ///
  /// In en, this message translates to:
  /// **'Levels'**
  String get levelsTitle;

  /// No description provided for @subjectsTitle.
  ///
  /// In en, this message translates to:
  /// **'Subjects'**
  String get subjectsTitle;

  /// No description provided for @levelsAndSubjects.
  ///
  /// In en, this message translates to:
  /// **'{levels} levels, {subjects} subjects'**
  String levelsAndSubjects(int levels, int subjects);

  /// No description provided for @catalogueUnavailable.
  ///
  /// In en, this message translates to:
  /// **'We could not load the list of curricula and zones. Connect once to download it; it is then kept on this phone.'**
  String get catalogueUnavailable;

  /// No description provided for @loadingCatalogue.
  ///
  /// In en, this message translates to:
  /// **'Loading curricula'**
  String get loadingCatalogue;

  /// No description provided for @loadingZones.
  ///
  /// In en, this message translates to:
  /// **'Loading zones'**
  String get loadingZones;

  /// No description provided for @documentsIntro.
  ///
  /// In en, this message translates to:
  /// **'You will need these documents. Uploading them comes next.'**
  String get documentsIntro;

  /// No description provided for @docNationalId.
  ///
  /// In en, this message translates to:
  /// **'National ID (front and back)'**
  String get docNationalId;

  /// No description provided for @docCertificates.
  ///
  /// In en, this message translates to:
  /// **'Academic certificates'**
  String get docCertificates;

  /// No description provided for @docRegistration.
  ///
  /// In en, this message translates to:
  /// **'Teaching certificate or registration'**
  String get docRegistration;

  /// No description provided for @docPolice.
  ///
  /// In en, this message translates to:
  /// **'Police Certificate of Good Conduct (or tell us you have applied)'**
  String get docPolice;

  /// No description provided for @docCv.
  ///
  /// In en, this message translates to:
  /// **'CV (optional)'**
  String get docCv;

  /// No description provided for @documentsLater.
  ///
  /// In en, this message translates to:
  /// **'Document upload is not part of this step yet. You can continue; we will ask for your documents before your application is checked.'**
  String get documentsLater;

  /// No description provided for @documentsReviewLine.
  ///
  /// In en, this message translates to:
  /// **'Documents are added after this step.'**
  String get documentsReviewLine;

  /// No description provided for @refereesIntro.
  ///
  /// In en, this message translates to:
  /// **'Add two people who know your teaching or character well. We may contact them to check your application.'**
  String get refereesIntro;

  /// No description provided for @refereeN.
  ///
  /// In en, this message translates to:
  /// **'Referee {n}'**
  String refereeN(int n);

  /// No description provided for @fieldRefereeName.
  ///
  /// In en, this message translates to:
  /// **'Full name'**
  String get fieldRefereeName;

  /// No description provided for @fieldRefereePhone.
  ///
  /// In en, this message translates to:
  /// **'Phone number'**
  String get fieldRefereePhone;

  /// No description provided for @refereePhoneHelp.
  ///
  /// In en, this message translates to:
  /// **'A Ugandan mobile number, for example 0701 234 567.'**
  String get refereePhoneHelp;

  /// No description provided for @fieldRelationship.
  ///
  /// In en, this message translates to:
  /// **'Relationship to you'**
  String get fieldRelationship;

  /// No description provided for @relHeadTeacher.
  ///
  /// In en, this message translates to:
  /// **'Head teacher'**
  String get relHeadTeacher;

  /// No description provided for @relColleague.
  ///
  /// In en, this message translates to:
  /// **'Colleague'**
  String get relColleague;

  /// No description provided for @relSupervisor.
  ///
  /// In en, this message translates to:
  /// **'Supervisor'**
  String get relSupervisor;

  /// No description provided for @relLecturer.
  ///
  /// In en, this message translates to:
  /// **'Lecturer'**
  String get relLecturer;

  /// No description provided for @relEmployer.
  ///
  /// In en, this message translates to:
  /// **'Employer'**
  String get relEmployer;

  /// No description provided for @relCommunityLeader.
  ///
  /// In en, this message translates to:
  /// **'Community leader'**
  String get relCommunityLeader;

  /// No description provided for @relOther.
  ///
  /// In en, this message translates to:
  /// **'Other'**
  String get relOther;

  /// No description provided for @fieldYearsKnown.
  ///
  /// In en, this message translates to:
  /// **'Years known'**
  String get fieldYearsKnown;

  /// No description provided for @fieldRefereeEmail.
  ///
  /// In en, this message translates to:
  /// **'Email (optional)'**
  String get fieldRefereeEmail;

  /// No description provided for @refereeConsent.
  ///
  /// In en, this message translates to:
  /// **'I confirm both referees agree that Home School may contact them about my application.'**
  String get refereeConsent;

  /// No description provided for @zonesTitle.
  ///
  /// In en, this message translates to:
  /// **'Where can you teach?'**
  String get zonesTitle;

  /// No description provided for @zonesHelp.
  ///
  /// In en, this message translates to:
  /// **'Pick the neighbourhoods you can travel to.'**
  String get zonesHelp;

  /// No description provided for @radiusTitle.
  ///
  /// In en, this message translates to:
  /// **'How far will you travel?'**
  String get radiusTitle;

  /// No description provided for @radiusValue.
  ///
  /// In en, this message translates to:
  /// **'{km} km'**
  String radiusValue(int km);

  /// No description provided for @radiusNotSet.
  ///
  /// In en, this message translates to:
  /// **'Not set'**
  String get radiusNotSet;

  /// No description provided for @radiusLess.
  ///
  /// In en, this message translates to:
  /// **'Less distance'**
  String get radiusLess;

  /// No description provided for @radiusMore.
  ///
  /// In en, this message translates to:
  /// **'More distance'**
  String get radiusMore;

  /// No description provided for @availabilityTitle.
  ///
  /// In en, this message translates to:
  /// **'Weekly availability'**
  String get availabilityTitle;

  /// No description provided for @availabilityHelp.
  ///
  /// In en, this message translates to:
  /// **'Tap the hours you can teach each week.'**
  String get availabilityHelp;

  /// No description provided for @peakLegend.
  ///
  /// In en, this message translates to:
  /// **'Highlighted hours are in high demand. Weekday evenings are the busiest.'**
  String get peakLegend;

  /// No description provided for @peakSemantic.
  ///
  /// In en, this message translates to:
  /// **'high demand'**
  String get peakSemantic;

  /// No description provided for @dayMon.
  ///
  /// In en, this message translates to:
  /// **'Mon'**
  String get dayMon;

  /// No description provided for @dayTue.
  ///
  /// In en, this message translates to:
  /// **'Tue'**
  String get dayTue;

  /// No description provided for @dayWed.
  ///
  /// In en, this message translates to:
  /// **'Wed'**
  String get dayWed;

  /// No description provided for @dayThu.
  ///
  /// In en, this message translates to:
  /// **'Thu'**
  String get dayThu;

  /// No description provided for @dayFri.
  ///
  /// In en, this message translates to:
  /// **'Fri'**
  String get dayFri;

  /// No description provided for @daySat.
  ///
  /// In en, this message translates to:
  /// **'Sat'**
  String get daySat;

  /// No description provided for @daySun.
  ///
  /// In en, this message translates to:
  /// **'Sun'**
  String get daySun;

  /// No description provided for @availabilityTotal.
  ///
  /// In en, this message translates to:
  /// **'{hours} hours a week'**
  String availabilityTotal(int hours);

  /// No description provided for @availabilityMinimum.
  ///
  /// In en, this message translates to:
  /// **'Offer at least {hours} hours a week.'**
  String availabilityMinimum(int hours);

  /// No description provided for @reviewIntro.
  ///
  /// In en, this message translates to:
  /// **'Check your details. Use Edit to change anything.'**
  String get reviewIntro;

  /// No description provided for @reviewComplete.
  ///
  /// In en, this message translates to:
  /// **'Everything needed is filled in.'**
  String get reviewComplete;

  /// No description provided for @reviewIncomplete.
  ///
  /// In en, this message translates to:
  /// **'Still to do: {sections}.'**
  String reviewIncomplete(String sections);

  /// No description provided for @sectionDone.
  ///
  /// In en, this message translates to:
  /// **'Complete'**
  String get sectionDone;

  /// No description provided for @sectionNeedsWork.
  ///
  /// In en, this message translates to:
  /// **'Needs attention'**
  String get sectionNeedsWork;

  /// No description provided for @declaration.
  ///
  /// In en, this message translates to:
  /// **'I declare the information in this application is true and complete.'**
  String get declaration;

  /// No description provided for @declarationNeeded.
  ///
  /// In en, this message translates to:
  /// **'Please tick the declaration to submit.'**
  String get declarationNeeded;

  /// No description provided for @submitApplication.
  ///
  /// In en, this message translates to:
  /// **'Submit application'**
  String get submitApplication;

  /// No description provided for @submitting.
  ///
  /// In en, this message translates to:
  /// **'Submitting...'**
  String get submitting;

  /// No description provided for @submitQueued.
  ///
  /// In en, this message translates to:
  /// **'Your application is ready. It will be sent as soon as you are online. You do not need to do anything.'**
  String get submitQueued;

  /// No description provided for @submitFailedIssues.
  ///
  /// In en, this message translates to:
  /// **'Please fix these before submitting:'**
  String get submitFailedIssues;

  /// No description provided for @statusSubmittedTitle.
  ///
  /// In en, this message translates to:
  /// **'Application submitted'**
  String get statusSubmittedTitle;

  /// No description provided for @statusSubmittedBody.
  ///
  /// In en, this message translates to:
  /// **'Thank you. Our team will check your application and contact you. You can leave this screen.'**
  String get statusSubmittedBody;

  /// No description provided for @statusUnderReviewTitle.
  ///
  /// In en, this message translates to:
  /// **'Your application is being reviewed'**
  String get statusUnderReviewTitle;

  /// No description provided for @statusApprovedTitle.
  ///
  /// In en, this message translates to:
  /// **'Application approved'**
  String get statusApprovedTitle;

  /// No description provided for @statusApprovedBody.
  ///
  /// In en, this message translates to:
  /// **'Welcome. Your approved curricula and levels will appear in the app.'**
  String get statusApprovedBody;

  /// No description provided for @statusNotApprovedTitle.
  ///
  /// In en, this message translates to:
  /// **'Application not approved this time'**
  String get statusNotApprovedTitle;

  /// No description provided for @statusNotApprovedBody.
  ///
  /// In en, this message translates to:
  /// **'Thank you for applying. You can apply again after a waiting period.'**
  String get statusNotApprovedBody;

  /// No description provided for @reapply.
  ///
  /// In en, this message translates to:
  /// **'Apply again'**
  String get reapply;

  /// No description provided for @reapplyOn.
  ///
  /// In en, this message translates to:
  /// **'You can apply again from {date}.'**
  String reapplyOn(String date);

  /// No description provided for @errRequired.
  ///
  /// In en, this message translates to:
  /// **'This is required.'**
  String get errRequired;

  /// No description provided for @errTooShort.
  ///
  /// In en, this message translates to:
  /// **'This is too short.'**
  String get errTooShort;

  /// No description provided for @errTooLong.
  ///
  /// In en, this message translates to:
  /// **'This is too long.'**
  String get errTooLong;

  /// No description provided for @errInvalid.
  ///
  /// In en, this message translates to:
  /// **'Please check this value.'**
  String get errInvalid;

  /// No description provided for @errUnknownChoice.
  ///
  /// In en, this message translates to:
  /// **'Please choose from the list.'**
  String get errUnknownChoice;

  /// No description provided for @errUnderAge.
  ///
  /// In en, this message translates to:
  /// **'You must be at least 18.'**
  String get errUnderAge;

  /// No description provided for @errOutOfRange.
  ///
  /// In en, this message translates to:
  /// **'This number is outside the allowed range.'**
  String get errOutOfRange;

  /// No description provided for @errDuplicate.
  ///
  /// In en, this message translates to:
  /// **'This is already used.'**
  String get errDuplicate;

  /// No description provided for @errOwnNumber.
  ///
  /// In en, this message translates to:
  /// **'A referee cannot have your own number.'**
  String get errOwnNumber;

  /// No description provided for @errOverlap.
  ///
  /// In en, this message translates to:
  /// **'These hours overlap with another block.'**
  String get errOverlap;

  /// No description provided for @errWrongCount.
  ///
  /// In en, this message translates to:
  /// **'Please give exactly two referees.'**
  String get errWrongCount;

  /// No description provided for @errTooMany.
  ///
  /// In en, this message translates to:
  /// **'You chose too many.'**
  String get errTooMany;

  /// No description provided for @errTooFewHours.
  ///
  /// In en, this message translates to:
  /// **'Offer at least {hours} hours a week.'**
  String errTooFewHours(int hours);
}

class _TeacherStringsDelegate extends LocalizationsDelegate<TeacherStrings> {
  const _TeacherStringsDelegate();

  @override
  Future<TeacherStrings> load(Locale locale) {
    return SynchronousFuture<TeacherStrings>(lookupTeacherStrings(locale));
  }

  @override
  bool isSupported(Locale locale) =>
      <String>['en'].contains(locale.languageCode);

  @override
  bool shouldReload(_TeacherStringsDelegate old) => false;
}

TeacherStrings lookupTeacherStrings(Locale locale) {
  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'en':
      return TeacherStringsEn();
  }

  throw FlutterError(
    'TeacherStrings.delegate failed to load unsupported locale "$locale". This is likely '
    'an issue with the localizations generation tool. Please file an issue '
    'on GitHub with a reproducible sample app and the gen-l10n configuration '
    'that was used.',
  );
}
