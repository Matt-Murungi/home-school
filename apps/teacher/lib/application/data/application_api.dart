import 'dart:async';
import 'dart:io';

import 'package:home_school_shared/home_school_shared.dart';

import '../model/application_draft.dart';
import '../model/reference.dart';

/// What the server knows about this teacher's application.
class ServerApplication {
  const ServerApplication({
    required this.status,
    required this.revision,
    required this.step,
    required this.attempt,
    required this.content,
    required this.canEdit,
    this.submittedAt,
    this.canReopen = false,
    this.reopenAt,
    this.issues = const [],
  });

  /// none | draft | submitted | under_review | action_needed | approved |
  /// not_approved
  final String status;
  final int revision;
  final int step;
  final int attempt;
  final ApplicationDraft content;
  final bool canEdit;
  final DateTime? submittedAt;
  final bool canReopen;
  final DateTime? reopenAt;
  final List<String> issues;

  bool get isSubmitted => const {
    'submitted',
    'under_review',
    'approved',
    'not_approved',
  }.contains(status);

  static ServerApplication fromJson(Map<String, Object?> j) =>
      ServerApplication(
        status: (j['status'] as String?) ?? 'none',
        revision: (j['revision'] as int?) ?? 0,
        step: (j['step'] as int?) ?? 0,
        attempt: (j['attempt'] as int?) ?? 0,
        content: ApplicationDraft.fromJson(j['content']),
        canEdit: j['can_edit'] != false,
        submittedAt: DateTime.tryParse((j['submitted_at'] as String?) ?? ''),
        canReopen: j['can_reopen'] == true,
        reopenAt: DateTime.tryParse((j['reopen_at'] as String?) ?? ''),
        issues: ((j['issues'] as List?) ?? const []).cast<String>(),
      );
}

sealed class ApplicationFailure implements Exception {
  const ApplicationFailure();
}

/// No network, timeout or 5xx: keep the draft locally and retry later.
class OfflineFailure extends ApplicationFailure {
  const OfflineFailure();
}

/// 409 draft_conflict: another phone saved a newer draft.
class DraftConflict extends ApplicationFailure {
  const DraftConflict(this.server);
  final ServerApplication server;
}

/// 409 application_locked / already_submitted / submit_in_progress and other
/// 4xx the user cannot fix by retrying.
class Rejected extends ApplicationFailure {
  const Rejected(this.code);
  final String code;
}

/// 422 incomplete: the server's validation of the final application.
class Incomplete extends ApplicationFailure {
  const Incomplete(this.issues);
  final List<String> issues;
}

class RateLimited extends ApplicationFailure {
  const RateLimited(this.retryAfter);
  final Duration retryAfter;
}

class SubmitResult {
  const SubmitResult({
    required this.status,
    required this.attempt,
    required this.replayed,
    this.submittedAt,
  });
  final String status;
  final int attempt;
  final bool replayed;
  final DateTime? submittedAt;
}

abstract interface class ApplicationApi {
  Future<ServerApplication> fetch();
  Future<ServerApplication> saveDraft({
    required int baseRevision,
    required int step,
    required ApplicationDraft draft,
  });
  Future<SubmitResult> submit({
    required int baseRevision,
    required String idempotencyKey,
  });
  Future<ServerApplication> reopen();
}

/// Where the published catalogue and zones come from. K01 (the catalogue read
/// API) is not built: the core service serves them at
/// `GET /v1/teacher/application/reference` until it is.
abstract interface class ReferenceSource {
  Future<ReferenceData> load();
}

/// Maps core-service responses to [ApplicationFailure]s. The ApiClient used
/// here must send the teacher's bearer token.
class HttpApplicationApi implements ApplicationApi, ReferenceSource {
  HttpApplicationApi(this._api);
  final ApiClient _api;
  static const _base = '/v1/teacher/application';

  Future<Map<String, Object?>> _send(
    String method,
    String path, {
    Map<String, Object?>? body,
    Map<String, String>? headers,
  }) async {
    final ApiResponse res;
    try {
      res = await _api.send(
        ApiRequest(
          method,
          '$_base$path',
          body: body,
          headers: headers ?? const {},
        ),
      );
    } on SocketException {
      throw const OfflineFailure();
    } on TimeoutException {
      throw const OfflineFailure();
    } on HttpException {
      throw const OfflineFailure();
    } on ApiException catch (e) {
      throw _map(e.statusCode, const {});
    }
    if (res.statusCode >= 200 && res.statusCode < 300) return res.body;
    final err = res.body['error'];
    throw _map(res.statusCode, err is Map ? err : const {});
  }

  static ApplicationFailure _map(int status, Map<Object?, Object?> err) {
    final code = (err['code'] as String?) ?? '';
    if (status == 409 && code == 'draft_conflict' && err['server'] is Map) {
      return DraftConflict(
        ServerApplication.fromJson(
          (err['server'] as Map).cast<String, Object?>(),
        ),
      );
    }
    if (status == 422 && code == 'incomplete') {
      return Incomplete(((err['issues'] as List?) ?? const []).cast<String>());
    }
    if (status == 429) {
      final s = err['retry_after_s'];
      return RateLimited(Duration(seconds: s is int ? s : 60));
    }
    if (status >= 500 || status == 408) return const OfflineFailure();
    return Rejected(code.isEmpty ? 'http_$status' : code);
  }

  @override
  Future<ServerApplication> fetch() async =>
      ServerApplication.fromJson(await _send('GET', ''));

  @override
  Future<ServerApplication> saveDraft({
    required int baseRevision,
    required int step,
    required ApplicationDraft draft,
  }) async => ServerApplication.fromJson(
    await _send(
      'PUT',
      '/draft',
      body: {
        'base_revision': baseRevision,
        'step': step,
        'content': draft.toJson(forServer: true),
      },
    ),
  );

  @override
  Future<SubmitResult> submit({
    required int baseRevision,
    required String idempotencyKey,
  }) async {
    final j = await _send(
      'POST',
      '/submit',
      body: {'base_revision': baseRevision, 'declaration': true},
      headers: {'Idempotency-Key': idempotencyKey},
    );
    return SubmitResult(
      status: (j['status'] as String?) ?? 'submitted',
      attempt: (j['attempt'] as int?) ?? 1,
      replayed: j['replayed'] == true,
      submittedAt: DateTime.tryParse((j['submitted_at'] as String?) ?? ''),
    );
  }

  @override
  Future<ServerApplication> reopen() async =>
      ServerApplication.fromJson(await _send('POST', '/reopen'));

  @override
  Future<ReferenceData> load() async =>
      ReferenceData.fromJson(await _send('GET', '/reference'));
}
