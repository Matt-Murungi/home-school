import 'dart:async';

import '../model/application_draft.dart';
import '../model/reference.dart';
import '../model/rules.dart';
import 'application_api.dart';

ReferenceData sampleReference() => const ReferenceData(
  curricula: [
    CurriculumOption(
      id: 'cur_ug',
      code: 'UG',
      name: 'Ugandan',
      levels: [
        NamedId('lvl_p1', 'Primary 1'),
        NamedId('lvl_p2', 'Primary 2'),
        NamedId('lvl_s1', 'Senior 1'),
      ],
      subjects: [
        NamedId('sub_math', 'Mathematics'),
        NamedId('sub_eng', 'English'),
        NamedId('sub_sci', 'Science'),
      ],
    ),
    CurriculumOption(
      id: 'cur_cam',
      code: 'CAM',
      name: 'Cambridge',
      levels: [NamedId('lvl_cp1', 'Stage 1'), NamedId('lvl_cp2', 'Stage 2')],
      subjects: [
        NamedId('sub_cmath', 'Mathematics'),
        NamedId('sub_cen', 'English'),
      ],
    ),
    CurriculumOption(
      id: 'cur_us',
      code: 'US',
      name: 'American',
      levels: [NamedId('lvl_g1', 'Grade 1'), NamedId('lvl_g2', 'Grade 2')],
      subjects: [NamedId('sub_umath', 'Math')],
    ),
  ],
  zones: [
    ZoneOption('zone_ntinda', 'Ntinda'),
    ZoneOption('zone_kira', 'Kira'),
    ZoneOption('zone_kololo', 'Kololo'),
    ZoneOption('zone_muyenga', 'Muyenga', hardToFill: true),
  ],
);

/// A complete, valid application (same facts as the server tests).
ApplicationDraft completeDraft() => const ApplicationDraft(
  personal: Personal(
    fullName: 'Grace Nakato',
    title: 'ms',
    gender: 'female',
    dateOfBirth: '1992-04-17',
    languages: ['en', 'lg'],
    photo: PhotoChoice(
      state: 'chosen',
      localPath: '/data/photo.jpg',
      crop: CropRect(0.1, 0.1, 0.8),
    ),
  ),
  qualifications: Qualifications(
    teacherType: 'registered',
    registrationNumber: 'UG/TR/12345',
    highestQualification: 'degree',
    institution: 'Makerere University',
    yearsTeaching: 6,
    currentSchool: 'Kampala Parents School',
  ),
  curricula: [
    CurriculumChoice(
      curriculumId: 'cur_ug',
      levelIds: ['lvl_p1', 'lvl_p2'],
      subjectIds: ['sub_math', 'sub_eng'],
    ),
  ],
  referees: [
    RefereeInput(
      name: 'Peter Okello',
      phone: '0701 234 567',
      relationship: 'head_teacher',
      yearsKnown: 4,
    ),
    RefereeInput(
      name: 'Sarah Namuli',
      phone: '+256 755 000 111',
      relationship: 'colleague',
      yearsKnown: 2,
    ),
  ],
  refereeConsent: true,
  zoneIds: ['zone_ntinda', 'zone_kira'],
  travelRadiusKm: 10,
  availability: [
    AvailabilitySlot(1, 900, 1080),
    AvailabilitySlot(3, 900, 1080),
  ],
);

/// In-memory stand-in for the core service with the real rules: revision
/// conflicts, locking, idempotent submit, validation, and network scenarios.
class FakeApplicationApi implements ApplicationApi, ReferenceSource {
  FakeApplicationApi({ReferenceData? reference})
    : reference = reference ?? sampleReference();

  ReferenceData reference;
  String status = 'none';
  int revision = 0;
  int step = 0;
  int attempt = 0;
  ApplicationDraft content = const ApplicationDraft();
  String? submitKey;
  DateTime? submittedAt;
  DateTime? reopenAt;

  /// While true every call fails like a dead network.
  bool offline = false;
  Failure? nextFailure;
  Duration latency = Duration.zero;
  DateTime today = DateTime.utc(2026, 11, 25);

  final calls = <String>[];
  int submitCount = 0; // distinct accepted submissions

  RuleContext get _ctx => RuleContext(today: today, reference: reference);

  Future<void> _gate(String name) async {
    calls.add(name);
    if (latency > Duration.zero) await Future<void>.delayed(latency);
    if (offline) throw const OfflineFailure();
    final f = nextFailure;
    if (f != null) {
      nextFailure = null;
      throw f.toException();
    }
  }

  ServerApplication _view({List<String> issues = const []}) =>
      ServerApplication(
        status: status,
        revision: revision,
        step: step,
        attempt: attempt,
        content: content,
        canEdit:
            status == 'none' || status == 'draft' || status == 'action_needed',
        submittedAt: submittedAt,
        canReopen: reopenAt != null && !today.isBefore(reopenAt!),
        reopenAt: reopenAt,
        issues: issues,
      );

  /// Another phone (or the web) changed the draft.
  void externalEdit(ApplicationDraft c) {
    content = c;
    revision++;
    if (status == 'none') status = 'draft';
  }

  @override
  Future<ServerApplication> fetch() async {
    await _gate('fetch');
    return _view();
  }

  @override
  Future<ServerApplication> saveDraft({
    required int baseRevision,
    required int step,
    required ApplicationDraft draft,
  }) async {
    await _gate('save');
    if (baseRevision != revision) throw DraftConflict(_view());
    if (!(status == 'none' || status == 'draft' || status == 'action_needed')) {
      throw const Rejected('application_locked');
    }
    final json = draft.toJson(forServer: true);
    content = ApplicationDraft.fromJson(json); // what the server stores
    this.step = step;
    revision++;
    if (status == 'none') status = 'draft';
    return _view(issues: validateApplication(content, _ctx, final_: false));
  }

  @override
  Future<SubmitResult> submit({
    required int baseRevision,
    required String idempotencyKey,
  }) async {
    await _gate('submit');
    if (const {'submitted', 'under_review', 'approved'}.contains(status)) {
      if (submitKey == idempotencyKey) {
        return SubmitResult(
          status: status,
          attempt: attempt,
          replayed: true,
          submittedAt: submittedAt,
        );
      }
      throw const Rejected('already_submitted');
    }
    if (baseRevision != revision) throw DraftConflict(_view());
    final issues = validateApplication(content, _ctx, final_: true);
    if (issues.isNotEmpty) throw Incomplete(issues);
    status = 'submitted';
    attempt++;
    submitCount++;
    revision++;
    submitKey = idempotencyKey;
    submittedAt = DateTime.utc(2026, 11, 25, 9);
    return SubmitResult(
      status: status,
      attempt: attempt,
      replayed: false,
      submittedAt: submittedAt,
    );
  }

  @override
  Future<ServerApplication> reopen() async {
    await _gate('reopen');
    if (status != 'not_approved') throw const Rejected('reopen_not_allowed');
    if (!_view().canReopen) throw const Rejected('reapply_too_soon');
    status = 'draft';
    revision++;
    return _view();
  }

  @override
  Future<ReferenceData> load() async {
    await _gate('reference');
    return reference;
  }
}

/// One-shot failure for the fake (a response the real server could send).
class Failure {
  const Failure.rateLimited(this.retryAfter) : kind = 'rate';
  const Failure.offline() : kind = 'offline', retryAfter = Duration.zero;
  const Failure.rejected() : kind = 'rejected', retryAfter = Duration.zero;
  final String kind;
  final Duration retryAfter;

  Exception toException() => switch (kind) {
    'rate' => RateLimited(retryAfter),
    'offline' => const OfflineFailure(),
    _ => const Rejected('forbidden'),
  };
}
