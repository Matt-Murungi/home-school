import 'dart:convert';

/// Chosen photo. [localPath] never leaves the device; the server only gets
/// [state] and [crop] (V02 will add the uploaded file reference).
class PhotoChoice {
  const PhotoChoice({
    required this.state,
    this.localPath,
    this.fileId,
    this.crop,
  });

  /// Always `chosen` (on this phone): V02 uploads the photo and owns file ids.
  final String state;
  final String? localPath;
  final String? fileId;
  final CropRect? crop;

  /// The server never gets a file id from the app (it could name someone
  /// else's file); [fileId] is only read back so the rules can flag it.
  Map<String, Object?> toJson({bool forServer = false}) => {
    'state': state,
    if (!forServer && fileId != null) 'file_id': fileId,
    if (crop != null) 'crop': crop!.toJson(),
    if (!forServer && localPath != null) 'local_path': localPath,
  };

  static PhotoChoice? fromJson(Object? j) {
    if (j is! Map) return null;
    return PhotoChoice(
      state: (j['state'] as String?) ?? 'chosen',
      localPath: j['local_path'] as String?,
      fileId: j['file_id'] as String?,
      crop: CropRect.fromJson(j['crop']),
    );
  }

  PhotoChoice withCrop(CropRect c) =>
      PhotoChoice(state: state, localPath: localPath, fileId: fileId, crop: c);
}

/// Square crop as fractions of the image (x, y of the top-left corner, side).
class CropRect {
  const CropRect(this.x, this.y, this.size);
  final double x;
  final double y;
  final double size;

  Map<String, Object?> toJson() => {'x': x, 'y': y, 'size': size};

  static CropRect? fromJson(Object? j) {
    if (j is! Map) return null;
    final x = j['x'], y = j['y'], s = j['size'];
    if (x is! num || y is! num || s is! num) return null;
    return CropRect(x.toDouble(), y.toDouble(), s.toDouble());
  }

  @override
  bool operator ==(Object other) =>
      other is CropRect && other.x == x && other.y == y && other.size == size;
  @override
  int get hashCode => Object.hash(x, y, size);
}

class Personal {
  const Personal({
    this.fullName,
    this.title,
    this.gender,
    this.dateOfBirth,
    this.languages,
    this.photo,
  });
  final String? fullName;
  final String? title;
  final String? gender;
  final String? dateOfBirth;
  final List<String>? languages;
  final PhotoChoice? photo;

  Personal copyWith({
    String? fullName,
    String? title,
    String? gender,
    String? dateOfBirth,
    List<String>? languages,
    PhotoChoice? photo,
    bool clearPhoto = false,
  }) => Personal(
    fullName: fullName ?? this.fullName,
    title: title ?? this.title,
    gender: gender ?? this.gender,
    dateOfBirth: dateOfBirth ?? this.dateOfBirth,
    languages: languages ?? this.languages,
    photo: clearPhoto ? null : (photo ?? this.photo),
  );

  Map<String, Object?> toJson({bool forServer = false}) => {
    if (fullName != null) 'full_name': fullName,
    if (title != null) 'title': title,
    if (gender != null) 'gender': gender,
    if (dateOfBirth != null) 'date_of_birth': dateOfBirth,
    if (languages != null) 'languages': languages,
    if (photo != null) 'photo': photo!.toJson(forServer: forServer),
  };

  static Personal fromJson(Object? j) {
    if (j is! Map) return const Personal();
    return Personal(
      fullName: j['full_name'] as String?,
      title: j['title'] as String?,
      gender: j['gender'] as String?,
      dateOfBirth: j['date_of_birth'] as String?,
      languages: (j['languages'] as List?)?.cast<String>(),
      photo: PhotoChoice.fromJson(j['photo']),
    );
  }
}

class Qualifications {
  const Qualifications({
    this.teacherType,
    this.registrationNumber,
    this.highestQualification,
    this.institution,
    this.yearsTeaching,
    this.currentSchool,
  });
  final String? teacherType;
  final String? registrationNumber;
  final String? highestQualification;
  final String? institution;
  final int? yearsTeaching;
  final String? currentSchool;

  Qualifications copyWith({
    String? teacherType,
    String? registrationNumber,
    String? highestQualification,
    String? institution,
    int? yearsTeaching,
    String? currentSchool,
    bool clearRegistration = false,
    bool clearYears = false,
  }) => Qualifications(
    teacherType: teacherType ?? this.teacherType,
    registrationNumber: clearRegistration
        ? null
        : (registrationNumber ?? this.registrationNumber),
    highestQualification: highestQualification ?? this.highestQualification,
    institution: institution ?? this.institution,
    yearsTeaching: clearYears ? null : (yearsTeaching ?? this.yearsTeaching),
    currentSchool: currentSchool ?? this.currentSchool,
  );

  Map<String, Object?> toJson() => {
    if (teacherType != null) 'teacher_type': teacherType,
    if (registrationNumber != null) 'registration_number': registrationNumber,
    if (highestQualification != null)
      'highest_qualification': highestQualification,
    if (institution != null) 'institution': institution,
    if (yearsTeaching != null) 'years_teaching': yearsTeaching,
    if (currentSchool != null) 'current_school': currentSchool,
  };

  static Qualifications fromJson(Object? j) {
    if (j is! Map) return const Qualifications();
    return Qualifications(
      teacherType: j['teacher_type'] as String?,
      registrationNumber: j['registration_number'] as String?,
      highestQualification: j['highest_qualification'] as String?,
      institution: j['institution'] as String?,
      yearsTeaching: j['years_teaching'] as int?,
      currentSchool: j['current_school'] as String?,
    );
  }
}

class CurriculumChoice {
  const CurriculumChoice({
    required this.curriculumId,
    this.levelIds = const [],
    this.subjectIds = const [],
  });
  final String curriculumId;
  final List<String> levelIds;
  final List<String> subjectIds;

  CurriculumChoice copyWith({
    List<String>? levelIds,
    List<String>? subjectIds,
  }) => CurriculumChoice(
    curriculumId: curriculumId,
    levelIds: levelIds ?? this.levelIds,
    subjectIds: subjectIds ?? this.subjectIds,
  );

  Map<String, Object?> toJson() => {
    'curriculum_id': curriculumId,
    'level_ids': levelIds,
    'subject_ids': subjectIds,
  };

  static CurriculumChoice fromJson(Map j) => CurriculumChoice(
    curriculumId: (j['curriculum_id'] as String?) ?? '',
    levelIds: ((j['level_ids'] as List?) ?? const []).cast<String>(),
    subjectIds: ((j['subject_ids'] as List?) ?? const []).cast<String>(),
  );
}

class RefereeInput {
  const RefereeInput({
    this.name,
    this.phone,
    this.relationship,
    this.email,
    this.yearsKnown,
  });
  final String? name;
  final String? phone;
  final String? relationship;
  final String? email;
  final int? yearsKnown;

  RefereeInput copyWith({
    String? name,
    String? phone,
    String? relationship,
    String? email,
    int? yearsKnown,
    bool clearYears = false,
  }) => RefereeInput(
    name: name ?? this.name,
    phone: phone ?? this.phone,
    relationship: relationship ?? this.relationship,
    email: email ?? this.email,
    yearsKnown: clearYears ? null : (yearsKnown ?? this.yearsKnown),
  );

  Map<String, Object?> toJson() => {
    if (name != null) 'name': name,
    if (phone != null) 'phone': phone,
    if (relationship != null) 'relationship': relationship,
    if (email != null) 'email': email,
    if (yearsKnown != null) 'years_known': yearsKnown,
  };

  static RefereeInput fromJson(Map j) => RefereeInput(
    name: j['name'] as String?,
    phone: j['phone'] as String?,
    relationship: j['relationship'] as String?,
    email: j['email'] as String?,
    yearsKnown: j['years_known'] as int?,
  );
}

/// One availability block: ISO weekday 1-7, minutes since midnight
/// (Africa/Kampala wall clock), on a 30 minute grid.
class AvailabilitySlot {
  const AvailabilitySlot(this.weekday, this.startMinute, this.endMinute);
  final int weekday;
  final int startMinute;
  final int endMinute;

  Map<String, Object?> toJson() => {
    'weekday': weekday,
    'start_minute': startMinute,
    'end_minute': endMinute,
  };

  static AvailabilitySlot fromJson(Map j) => AvailabilitySlot(
    j['weekday'] as int,
    j['start_minute'] as int,
    j['end_minute'] as int,
  );

  @override
  bool operator ==(Object other) =>
      other is AvailabilitySlot &&
      other.weekday == weekday &&
      other.startMinute == startMinute &&
      other.endMinute == endMinute;
  @override
  int get hashCode => Object.hash(weekday, startMinute, endMinute);
}

/// The whole wizard payload, exactly the server's `content` object.
class ApplicationDraft {
  const ApplicationDraft({
    this.personal = const Personal(),
    this.qualifications = const Qualifications(),
    this.curricula = const [],
    this.referees = const [],
    this.refereeConsent = false,
    this.zoneIds = const [],
    this.travelRadiusKm,
    this.availability = const [],
  });
  final Personal personal;
  final Qualifications qualifications;
  final List<CurriculumChoice> curricula;
  final List<RefereeInput> referees;
  final bool refereeConsent;
  final List<String> zoneIds;
  final int? travelRadiusKm;
  final List<AvailabilitySlot> availability;

  ApplicationDraft copyWith({
    Personal? personal,
    Qualifications? qualifications,
    List<CurriculumChoice>? curricula,
    List<RefereeInput>? referees,
    bool? refereeConsent,
    List<String>? zoneIds,
    int? travelRadiusKm,
    List<AvailabilitySlot>? availability,
  }) => ApplicationDraft(
    personal: personal ?? this.personal,
    qualifications: qualifications ?? this.qualifications,
    curricula: curricula ?? this.curricula,
    referees: referees ?? this.referees,
    refereeConsent: refereeConsent ?? this.refereeConsent,
    zoneIds: zoneIds ?? this.zoneIds,
    travelRadiusKm: travelRadiusKm ?? this.travelRadiusKm,
    availability: availability ?? this.availability,
  );

  bool get isEmpty =>
      jsonEncode(toJson()) == jsonEncode(const ApplicationDraft().toJson());

  /// `forServer` strips device-only values (the local photo path).
  Map<String, Object?> toJson({bool forServer = false}) => {
    'personal': personal.toJson(forServer: forServer),
    'qualifications': qualifications.toJson(),
    'curricula': [for (final c in curricula) c.toJson()],
    'referees': [for (final r in referees) r.toJson()],
    'referee_consent': refereeConsent,
    'zone_ids': zoneIds,
    if (travelRadiusKm != null) 'travel_radius_km': travelRadiusKm,
    'availability': [for (final s in availability) s.toJson()],
  };

  static ApplicationDraft fromJson(Object? json) {
    if (json is! Map) return const ApplicationDraft();
    return ApplicationDraft(
      personal: Personal.fromJson(json['personal']),
      qualifications: Qualifications.fromJson(json['qualifications']),
      curricula: [
        for (final c in (json['curricula'] as List?) ?? const [])
          CurriculumChoice.fromJson(c as Map),
      ],
      referees: [
        for (final r in (json['referees'] as List?) ?? const [])
          RefereeInput.fromJson(r as Map),
      ],
      refereeConsent: json['referee_consent'] == true,
      zoneIds: ((json['zone_ids'] as List?) ?? const []).cast<String>(),
      travelRadiusKm: json['travel_radius_km'] as int?,
      availability: [
        for (final s in (json['availability'] as List?) ?? const [])
          AvailabilitySlot.fromJson(s as Map),
      ],
    );
  }
}
