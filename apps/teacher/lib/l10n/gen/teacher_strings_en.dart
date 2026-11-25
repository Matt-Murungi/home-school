// ignore: unused_import
import 'package:intl/intl.dart' as intl;

import 'teacher_strings.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class TeacherStringsEn extends TeacherStrings {
  TeacherStringsEn([String locale = 'en']) : super(locale);

  @override
  String get applyTitle => 'Apply to teach';

  @override
  String get loading => 'Loading';

  @override
  String get loadFailed =>
      'We could not load your application and there is no copy on this phone yet. Check your connection and try again.';

  @override
  String get tryAgain => 'Try again';

  @override
  String get back => 'Back';

  @override
  String get next => 'Next';

  @override
  String get edit => 'Edit';

  @override
  String get goHome => 'Go to the app';

  @override
  String stepOf(int step, int total) {
    return 'Step $step of $total';
  }

  @override
  String progressSemantic(int step, int total, String title) {
    return 'Step $step of $total: $title';
  }

  @override
  String get stepPersonal => 'Personal details';

  @override
  String get stepQualifications => 'Qualifications and curricula';

  @override
  String get stepDocuments => 'Documents';

  @override
  String get stepReferees => 'Referees';

  @override
  String get stepZones => 'Zones and availability';

  @override
  String get stepReview => 'Review and submit';

  @override
  String get syncSaved => 'Saved. You can leave and come back later.';

  @override
  String get syncSaving => 'Saving...';

  @override
  String get syncOffline =>
      'Saved on this phone. We will send it when you are online.';

  @override
  String get syncRateLimited =>
      'Saved on this phone. Too many saves; we will retry shortly.';

  @override
  String get syncConflict =>
      'This application was changed somewhere else. Choose which version to keep.';

  @override
  String get syncLocked => 'This application can no longer be edited.';

  @override
  String get conflictTitle => 'Two versions of your application';

  @override
  String get conflictBody =>
      'You made changes on this phone while the saved version changed elsewhere. Keep the version on this phone, or use the saved one.';

  @override
  String get conflictKeepMine => 'Keep this phone\'s version';

  @override
  String get conflictUseTheirs => 'Use the saved version';

  @override
  String get fieldFullName => 'Full name';

  @override
  String get fieldTitle => 'Preferred title';

  @override
  String get titleMr => 'Mr';

  @override
  String get titleMs => 'Ms';

  @override
  String get titleTeacher => 'Teacher';

  @override
  String get fieldGender => 'Gender';

  @override
  String get genderFemale => 'Female';

  @override
  String get genderMale => 'Male';

  @override
  String get fieldDob => 'Date of birth';

  @override
  String get dobHelp => 'You must be at least 18. Only our team sees this.';

  @override
  String get dobPlaceholder => 'Choose a date';

  @override
  String get fieldLanguages => 'Languages you speak';

  @override
  String get langEnglish => 'English';

  @override
  String get langLuganda => 'Luganda';

  @override
  String get langSwahili => 'Swahili';

  @override
  String get langRunyankole => 'Runyankole / Rukiga';

  @override
  String get langLusoga => 'Lusoga';

  @override
  String get langAteso => 'Ateso';

  @override
  String get langAcholi => 'Acholi';

  @override
  String get langOther => 'Other';

  @override
  String get photoTitle => 'Your photo';

  @override
  String get photoGuidanceTitle => 'A good photo';

  @override
  String get photoGuide1 => 'Plain, light background.';

  @override
  String get photoGuide2 => 'Shoulders up, facing the camera.';

  @override
  String get photoGuide3 => 'Good light on your face; no sunglasses or hat.';

  @override
  String get photoGuide4 => 'Just you in the photo.';

  @override
  String get photoTake => 'Take a photo';

  @override
  String get photoChoose => 'Choose from phone';

  @override
  String get photoAdjust => 'Adjust crop';

  @override
  String get photoRemove => 'Remove photo';

  @override
  String get photoPickFailed =>
      'We could not open the camera or photos. Check the app\'s permissions and try again.';

  @override
  String get photoPreviewLabel => 'Your chosen photo';

  @override
  String get photoAdded => 'Photo added';

  @override
  String get photoMissing => 'Photo missing';

  @override
  String get cropTitle => 'Crop your photo';

  @override
  String get cropHelp =>
      'Zoom and move the square so your face and shoulders are in the middle.';

  @override
  String get cropZoom => 'Zoom';

  @override
  String get cropLeftRight => 'Move left or right';

  @override
  String get cropUpDown => 'Move up or down';

  @override
  String get cropDone => 'Use this crop';

  @override
  String get fieldTeacherType => 'I am a';

  @override
  String get typeRegistered => 'Registered teacher';

  @override
  String get typeGraduate => 'Graduate';

  @override
  String get fieldRegistration => 'Teacher registration number';

  @override
  String get fieldRegistrationOptional => 'Registration number (optional)';

  @override
  String get registrationGraduateHelp => 'Graduates can leave this empty.';

  @override
  String get fieldHighestQualification => 'Highest qualification';

  @override
  String get qualCertificate => 'Certificate';

  @override
  String get qualDiploma => 'Diploma';

  @override
  String get qualDegree => 'Degree';

  @override
  String get qualPostgraduate => 'Postgraduate diploma';

  @override
  String get qualMasters => 'Master\'s degree';

  @override
  String get qualDoctorate => 'Doctorate';

  @override
  String get fieldInstitution => 'Institution';

  @override
  String get fieldYearsTeaching => 'Years of teaching';

  @override
  String get fieldCurrentSchool => 'Current school (optional)';

  @override
  String get currentSchoolHelp =>
      'Only our admin team sees this. It is never shown to families.';

  @override
  String get curriculaTitle => 'Curricula you want to teach';

  @override
  String get curriculaHelp =>
      'Pick each curriculum, then the levels and subjects you can teach. Our team approves them after review.';

  @override
  String get levelsTitle => 'Levels';

  @override
  String get subjectsTitle => 'Subjects';

  @override
  String levelsAndSubjects(int levels, int subjects) {
    return '$levels levels, $subjects subjects';
  }

  @override
  String get catalogueUnavailable =>
      'We could not load the list of curricula and zones. Connect once to download it; it is then kept on this phone.';

  @override
  String get loadingCatalogue => 'Loading curricula';

  @override
  String get loadingZones => 'Loading zones';

  @override
  String get documentsIntro =>
      'You will need these documents. Uploading them comes next.';

  @override
  String get docNationalId => 'National ID (front and back)';

  @override
  String get docCertificates => 'Academic certificates';

  @override
  String get docRegistration => 'Teaching certificate or registration';

  @override
  String get docPolice =>
      'Police Certificate of Good Conduct (or tell us you have applied)';

  @override
  String get docCv => 'CV (optional)';

  @override
  String get documentsLater =>
      'Document upload is not part of this step yet. You can continue; we will ask for your documents before your application is checked.';

  @override
  String get documentsReviewLine => 'Documents are added after this step.';

  @override
  String get refereesIntro =>
      'Add two people who know your teaching or character well. We may contact them to check your application.';

  @override
  String refereeN(int n) {
    return 'Referee $n';
  }

  @override
  String get fieldRefereeName => 'Full name';

  @override
  String get fieldRefereePhone => 'Phone number';

  @override
  String get refereePhoneHelp =>
      'A Ugandan mobile number, for example 0701 234 567.';

  @override
  String get fieldRelationship => 'Relationship to you';

  @override
  String get relHeadTeacher => 'Head teacher';

  @override
  String get relColleague => 'Colleague';

  @override
  String get relSupervisor => 'Supervisor';

  @override
  String get relLecturer => 'Lecturer';

  @override
  String get relEmployer => 'Employer';

  @override
  String get relCommunityLeader => 'Community leader';

  @override
  String get relOther => 'Other';

  @override
  String get fieldYearsKnown => 'Years known';

  @override
  String get fieldRefereeEmail => 'Email (optional)';

  @override
  String get refereeConsent =>
      'I confirm both referees agree that Home School may contact them about my application.';

  @override
  String get zonesTitle => 'Where can you teach?';

  @override
  String get zonesHelp => 'Pick the neighbourhoods you can travel to.';

  @override
  String get radiusTitle => 'How far will you travel?';

  @override
  String radiusValue(int km) {
    return '$km km';
  }

  @override
  String get radiusNotSet => 'Not set';

  @override
  String get radiusLess => 'Less distance';

  @override
  String get radiusMore => 'More distance';

  @override
  String get availabilityTitle => 'Weekly availability';

  @override
  String get availabilityHelp => 'Tap the hours you can teach each week.';

  @override
  String get peakLegend =>
      'Highlighted hours are in high demand. Weekday evenings are the busiest.';

  @override
  String get peakSemantic => 'high demand';

  @override
  String get dayMon => 'Mon';

  @override
  String get dayTue => 'Tue';

  @override
  String get dayWed => 'Wed';

  @override
  String get dayThu => 'Thu';

  @override
  String get dayFri => 'Fri';

  @override
  String get daySat => 'Sat';

  @override
  String get daySun => 'Sun';

  @override
  String availabilityTotal(int hours) {
    return '$hours hours a week';
  }

  @override
  String availabilityMinimum(int hours) {
    return 'Offer at least $hours hours a week.';
  }

  @override
  String get reviewIntro => 'Check your details. Use Edit to change anything.';

  @override
  String get reviewComplete => 'Everything needed is filled in.';

  @override
  String reviewIncomplete(String sections) {
    return 'Still to do: $sections.';
  }

  @override
  String get sectionDone => 'Complete';

  @override
  String get sectionNeedsWork => 'Needs attention';

  @override
  String get declaration =>
      'I declare the information in this application is true and complete.';

  @override
  String get declarationNeeded => 'Please tick the declaration to submit.';

  @override
  String get submitApplication => 'Submit application';

  @override
  String get submitting => 'Submitting...';

  @override
  String get submitQueued =>
      'Your application is ready. It will be sent as soon as you are online. You do not need to do anything.';

  @override
  String get submitFailedIssues => 'Please fix these before submitting:';

  @override
  String get statusSubmittedTitle => 'Application submitted';

  @override
  String get statusSubmittedBody =>
      'Thank you. Our team will check your application and contact you. You can leave this screen.';

  @override
  String get statusUnderReviewTitle => 'Your application is being reviewed';

  @override
  String get statusApprovedTitle => 'Application approved';

  @override
  String get statusApprovedBody =>
      'Welcome. Your approved curricula and levels will appear in the app.';

  @override
  String get statusNotApprovedTitle => 'Application not approved this time';

  @override
  String get statusNotApprovedBody =>
      'Thank you for applying. You can apply again after a waiting period.';

  @override
  String get reapply => 'Apply again';

  @override
  String reapplyOn(String date) {
    return 'You can apply again from $date.';
  }

  @override
  String get errRequired => 'This is required.';

  @override
  String get errTooShort => 'This is too short.';

  @override
  String get errTooLong => 'This is too long.';

  @override
  String get errInvalid => 'Please check this value.';

  @override
  String get errUnknownChoice => 'Please choose from the list.';

  @override
  String get errUnderAge => 'You must be at least 18.';

  @override
  String get errOutOfRange => 'This number is outside the allowed range.';

  @override
  String get errDuplicate => 'This is already used.';

  @override
  String get errOwnNumber => 'A referee cannot have your own number.';

  @override
  String get errOverlap => 'These hours overlap with another block.';

  @override
  String get errWrongCount => 'Please give exactly two referees.';

  @override
  String get errTooMany => 'You chose too many.';

  @override
  String errTooFewHours(int hours) {
    return 'Offer at least $hours hours a week.';
  }

  @override
  String get submitInProgress =>
      'We are still finishing your earlier submission. This can take a few minutes; we will keep trying and you do not need to do anything.';
}
