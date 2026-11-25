import 'package:home_school_shared/home_school_shared.dart'
    show normalizeUgPhone;

import 'application_draft.dart';
import 'reference.dart';

/// Validation rules of the application (VER-1). Mirrors
/// `services/core/src/hs_core/teacher_application/rules.py`; both are tested
/// against `test/fixtures/application_cases.json`. Issues are `path:code`.
const titles = ['mr', 'ms', 'teacher'];
const genders = ['female', 'male'];
const languageCodes = ['en', 'lg', 'sw', 'nyn', 'xog', 'teo', 'ach', 'other'];
const teacherTypes = ['registered', 'graduate'];
const qualificationLevels = [
  'certificate',
  'diploma',
  'degree',
  'postgraduate',
  'masters',
  'doctorate',
];
const relationships = [
  'head_teacher',
  'colleague',
  'supervisor',
  'lecturer',
  'employer',
  'community_leader',
  'other',
];
const minAge = 18;
const maxAge = 80;
const maxCurricula = 3;
const maxZones = 10;
const radiusRange = (1, 50);
const yearsRange = (0, 60);
const dayStart = 6 * 60;
const dayEnd = 21 * 60;
const gridMinutes = 30;
const maxSlots = 70;
const defaultMinWeeklyHours = 6;

/// Protected peak windows (NFR-5): weekdays 15:00-20:00, weekends 08:00-18:00.
(int, int) peakWindow(int weekday) =>
    weekday <= 5 ? (15 * 60, 20 * 60) : (8 * 60, 18 * 60);

bool isPeak(int weekday, int start, int end) {
  final (lo, hi) = peakWindow(weekday);
  return start < hi && end > lo;
}

class RuleContext {
  const RuleContext({
    required this.today,
    required this.reference,
    this.ownPhone,
    this.minWeeklyMinutes = defaultMinWeeklyHours * 60,
  });
  final DateTime today; // Kampala calendar date (time part ignored)
  final ReferenceData reference;
  final String? ownPhone;
  final int minWeeklyMinutes;
}

final _bad = RegExp(
  r'[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]',
  unicode: true,
);
final _letter = RegExp(r'\p{L}', unicode: true);
final _space = RegExp(r'[\p{Zs}\t\n\r]', unicode: true);
final _spaces = RegExp(r' {2,}');
final _reg = RegExp(r'^[A-Za-z0-9][A-Za-z0-9/ .\-]{1,38}[A-Za-z0-9]$');
final _email = RegExp(r'^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,24}$');
final _date = RegExp(r'^\d{4}-\d{2}-\d{2}$');

/// Same cleaning as the server (minus Unicode NFC, which Dart lacks).
String cleanText(String? v) =>
    (v ?? '').replaceAll(_space, ' ').replaceAll(_spaces, ' ').trim();

int _len(String s) => s.runes.length;

String _text(
  String path,
  String? raw,
  int lo,
  int hi,
  List<String> out, {
  required bool required,
}) {
  final s = cleanText(raw);
  if (s.isEmpty) {
    if (required) out.add('$path:required');
    return s;
  }
  if (_bad.hasMatch(s) || !_letter.hasMatch(s)) {
    out.add('$path:invalid');
  } else if (_len(s) < lo) {
    out.add('$path:too_short');
  } else if (_len(s) > hi) {
    out.add('$path:too_long');
  }
  return s;
}

void _choice(
  String path,
  String? raw,
  List<String> allowed,
  List<String> out,
  bool required,
) {
  if (raw == null || raw.isEmpty) {
    if (required) out.add('$path:required');
  } else if (!allowed.contains(raw)) {
    out.add('$path:unknown_choice');
  }
}

DateTime? parseIsoDate(String? raw) {
  if (raw == null || !_date.hasMatch(raw)) return null;
  final y = int.parse(raw.substring(0, 4));
  final m = int.parse(raw.substring(5, 7));
  final d = int.parse(raw.substring(8, 10));
  final dt = DateTime.utc(y, m, d);
  if (dt.year != y || dt.month != m || dt.day != d) return null;
  return dt;
}

int ageOn(DateTime dob, DateTime today) {
  var a = today.year - dob.year;
  if (today.month < dob.month ||
      (today.month == dob.month && today.day < dob.day)) {
    a--;
  }
  return a;
}

void _personal(Personal p, RuleContext ctx, bool final_, List<String> out) {
  _text('personal.full_name', p.fullName, 2, 100, out, required: final_);
  _choice('personal.title', p.title, titles, out, final_);
  _choice('personal.gender', p.gender, genders, out, final_);
  final dobRaw = p.dateOfBirth;
  if (dobRaw == null || dobRaw.isEmpty) {
    if (final_) out.add('personal.date_of_birth:required');
  } else {
    final d = parseIsoDate(dobRaw);
    if (d == null || d.isAfter(ctx.today)) {
      out.add('personal.date_of_birth:invalid');
    } else {
      final a = ageOn(d, ctx.today);
      if (a < minAge) {
        out.add('personal.date_of_birth:under_age');
      } else if (a > maxAge) {
        out.add('personal.date_of_birth:invalid');
      }
    }
  }
  final langs = p.languages;
  if (langs == null || langs.isEmpty) {
    if (final_) out.add('personal.languages:required');
  } else {
    if (langs.length > languageCodes.length) {
      out.add('personal.languages:too_many');
    }
    if (langs.any((x) => !languageCodes.contains(x))) {
      out.add('personal.languages:unknown_choice');
    }
    if (langs.toSet().length != langs.length) {
      out.add('personal.languages:duplicate');
    }
  }
  final photo = p.photo;
  if (photo == null) {
    if (final_) out.add('personal.photo:required');
  } else if (photo.state == 'uploaded' && (photo.fileId ?? '').isEmpty) {
    out.add('personal.photo:invalid');
  } else if (photo.crop != null &&
      (photo.crop!.x + photo.crop!.size > 1.0001 ||
          photo.crop!.y + photo.crop!.size > 1.0001)) {
    out.add('personal.photo:invalid');
  }
}

void _qualifications(Qualifications q, bool final_, List<String> out) {
  _choice(
    'qualifications.teacher_type',
    q.teacherType,
    teacherTypes,
    out,
    final_,
  );
  final reg = cleanText(q.registrationNumber);
  if (reg.isNotEmpty) {
    if (_bad.hasMatch(reg) || !_reg.hasMatch(reg)) {
      out.add('qualifications.registration_number:invalid');
    }
  } else if (q.teacherType == 'registered' && final_) {
    out.add('qualifications.registration_number:required');
  }
  _choice(
    'qualifications.highest_qualification',
    q.highestQualification,
    qualificationLevels,
    out,
    final_,
  );
  _text(
    'qualifications.institution',
    q.institution,
    2,
    120,
    out,
    required: final_,
  );
  final y = q.yearsTeaching;
  if (y == null) {
    if (final_) out.add('qualifications.years_teaching:required');
  } else if (y < yearsRange.$1 || y > yearsRange.$2) {
    out.add('qualifications.years_teaching:out_of_range');
  }
  _text(
    'qualifications.current_school',
    q.currentSchool,
    2,
    120,
    out,
    required: false,
  );
}

void _curricula(
  List<CurriculumChoice> items,
  RuleContext ctx,
  bool final_,
  List<String> out,
) {
  if (items.isEmpty) {
    if (final_) out.add('curricula:required');
    return;
  }
  if (items.length > maxCurricula) out.add('curricula:too_many');
  final seen = <String>{};
  for (var i = 0; i < items.length; i++) {
    final c = items[i];
    final base = 'curricula[$i]';
    final opt = ctx.reference.curriculum(c.curriculumId);
    if (opt == null) out.add('$base.curriculum_id:unknown_choice');
    if (!seen.add(c.curriculumId)) out.add('$base.curriculum_id:duplicate');
    for (final (key, ids, valid) in [
      (
        'level_ids',
        c.levelIds,
        {for (final l in opt?.levels ?? <NamedId>[]) l.id},
      ),
      (
        'subject_ids',
        c.subjectIds,
        {for (final s in opt?.subjects ?? <NamedId>[]) s.id},
      ),
    ]) {
      if (ids.isEmpty) {
        if (final_) out.add('$base.$key:required');
      } else if (opt != null && ids.any((x) => !valid.contains(x))) {
        out.add('$base.$key:unknown_choice');
      } else if (ids.toSet().length != ids.length) {
        out.add('$base.$key:duplicate');
      }
    }
  }
}

void _referees(
  List<RefereeInput> items,
  bool consent,
  RuleContext ctx,
  bool final_,
  List<String> out,
) {
  if ((final_ && items.length != 2) || items.length > 2) {
    out.add('referees:wrong_count');
  }
  final phones = <String>[];
  for (var i = 0; i < items.length; i++) {
    final r = items[i];
    final b = 'referees[$i]';
    _text('$b.name', r.name, 2, 100, out, required: final_);
    _choice('$b.relationship', r.relationship, relationships, out, final_);
    final raw = r.phone;
    if (raw == null || raw.isEmpty) {
      if (final_) out.add('$b.phone:required');
    } else {
      final e164 = normalizeUgPhone(raw);
      if (e164 == null) {
        out.add('$b.phone:invalid');
      } else {
        phones.add(e164);
        if (e164 == ctx.ownPhone) out.add('$b.phone:own_number');
      }
    }
    final yk = r.yearsKnown;
    if (yk == null) {
      if (final_) out.add('$b.years_known:required');
    } else if (yk < 1 || yk > 60) {
      out.add('$b.years_known:out_of_range');
    }
    final email = cleanText(r.email);
    if (email.isNotEmpty && (!_email.hasMatch(email) || _bad.hasMatch(email))) {
      out.add('$b.email:invalid');
    }
  }
  if (phones.length != phones.toSet().length) {
    out.add('referees[1].phone:duplicate');
  }
  if (final_ && !consent) out.add('referee_consent:required');
}

int weeklyMinutes(List<AvailabilitySlot> slots) =>
    slots.fold(0, (a, s) => a + s.endMinute - s.startMinute);

void _availability(
  List<AvailabilitySlot> slots,
  RuleContext ctx,
  bool final_,
  List<String> out,
) {
  if (slots.isEmpty) {
    if (final_) out.add('availability:required');
    return;
  }
  if (slots.length > maxSlots) {
    out.add('availability:too_many');
    return;
  }
  final ok = <(int, int, int, int)>[];
  for (var i = 0; i < slots.length; i++) {
    final s = slots[i];
    final bad =
        s.weekday < 1 ||
        s.weekday > 7 ||
        s.startMinute % gridMinutes != 0 ||
        s.endMinute % gridMinutes != 0 ||
        s.startMinute >= s.endMinute ||
        s.startMinute < dayStart ||
        s.endMinute > dayEnd;
    if (bad) {
      out.add('availability[$i]:invalid');
    } else {
      ok.add((s.weekday, s.startMinute, s.endMinute, i));
    }
  }
  ok.sort((a, b) {
    for (final c in [
      a.$1.compareTo(b.$1),
      a.$2.compareTo(b.$2),
      a.$3.compareTo(b.$3),
    ]) {
      if (c != 0) return c;
    }
    return a.$4.compareTo(b.$4);
  });
  var overlap = false;
  for (var i = 1; i < ok.length; i++) {
    if (ok[i - 1].$1 == ok[i].$1 && ok[i].$2 < ok[i - 1].$3) {
      out.add('availability[${ok[i].$4}]:overlap');
      overlap = true;
    }
  }
  if (final_ &&
      ok.length == slots.length &&
      !overlap &&
      weeklyMinutes(slots) < ctx.minWeeklyMinutes) {
    out.add('availability:too_few_hours');
  }
}

void _zones(
  List<String> ids,
  int? radius,
  RuleContext ctx,
  bool final_,
  List<String> out,
) {
  if (ids.isEmpty) {
    if (final_) out.add('zone_ids:required');
  } else {
    if (ids.length > maxZones) out.add('zone_ids:too_many');
    if (ids.toSet().length != ids.length) out.add('zone_ids:duplicate');
    final known = {for (final z in ctx.reference.zones) z.id};
    if (ids.any((z) => !known.contains(z))) out.add('zone_ids:unknown_choice');
  }
  if (radius == null) {
    if (final_) out.add('travel_radius_km:required');
  } else if (radius < radiusRange.$1 || radius > radiusRange.$2) {
    out.add('travel_radius_km:out_of_range');
  }
}

/// [final_] true = submit (also reports what is missing); false = draft.
List<String> validateApplication(
  ApplicationDraft d,
  RuleContext ctx, {
  required bool final_,
}) {
  final out = <String>[];
  _personal(d.personal, ctx, final_, out);
  _qualifications(d.qualifications, final_, out);
  _curricula(d.curricula, ctx, final_, out);
  _referees(d.referees, d.refereeConsent, ctx, final_, out);
  _zones(d.zoneIds, d.travelRadiusKm, ctx, final_, out);
  _availability(d.availability, ctx, final_, out);
  return out;
}

/// Wizard steps (T-03 .. T-08).
enum WizardStep { personal, qualifications, documents, referees, zones, review }

/// Which step an issue path belongs to (for "fix this" links on the review).
WizardStep stepOfIssue(String issue) {
  final path = issue.split(':').first;
  if (path.startsWith('personal')) return WizardStep.personal;
  if (path.startsWith('qualifications') || path.startsWith('curricula')) {
    return WizardStep.qualifications;
  }
  if (path.startsWith('referee')) return WizardStep.referees;
  return WizardStep.zones; // zones, travel radius, availability
}
