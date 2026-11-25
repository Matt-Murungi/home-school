import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/providers.dart';
import 'auth_service.dart';
import 'otp_models.dart';
import 'phone.dart';

enum OtpStep { phone, code }

/// State of the shared phone sign-in flow (P-02, P-03, T-02).
class OtpFlowState {
  const OtpFlowState({
    this.step = OtpStep.phone,
    this.e164,
    this.busy = false,
    this.failure,
    this.attemptsLeft,
    this.retryAfter,
    this.resendSeconds = 0,
    this.smsDelayed = false,
    this.session,
  });

  final OtpStep step;

  /// Held in memory only for the current sign-in; never persisted.
  final String? e164;
  final bool busy;
  final OtpFailure? failure;
  final int? attemptsLeft;
  final Duration? retryAfter;
  final int resendSeconds;

  /// The resend countdown ran out and the user still has no code to enter.
  final bool smsDelayed;
  final AuthSession? session;

  bool get canResend => resendSeconds == 0 && !busy;

  OtpFlowState copyWith({
    OtpStep? step,
    String? e164,
    bool? busy,
    OtpFailure? failure,
    bool clearFailure = false,
    int? attemptsLeft,
    Duration? retryAfter,
    int? resendSeconds,
    bool? smsDelayed,
    AuthSession? session,
  }) => OtpFlowState(
    step: step ?? this.step,
    e164: e164 ?? this.e164,
    busy: busy ?? this.busy,
    failure: clearFailure ? null : (failure ?? this.failure),
    attemptsLeft: clearFailure ? null : (attemptsLeft ?? this.attemptsLeft),
    retryAfter: clearFailure ? null : (retryAfter ?? this.retryAfter),
    resendSeconds: resendSeconds ?? this.resendSeconds,
    smsDelayed: smsDelayed ?? this.smsDelayed,
    session: session ?? this.session,
  );
}

final otpFlowProvider = NotifierProvider<OtpFlowController, OtpFlowState>(
  OtpFlowController.new,
);

class OtpFlowController extends Notifier<OtpFlowState> {
  Timer? _timer;
  bool _verifying = false;

  @override
  OtpFlowState build() {
    ref.onDispose(() => _timer?.cancel());
    return const OtpFlowState();
  }

  AuthService get _auth => ref.read(authServiceProvider);

  /// Back to a clean phone entry (entering the flow, or after sign-out).
  void reset() {
    _timer?.cancel();
    _verifying = false;
    state = const OtpFlowState();
  }

  void clearError() {
    if (state.failure != null) state = state.copyWith(clearFailure: true);
  }

  /// Validates [raw] (what the user typed) and asks for a code.
  Future<void> submitPhone(String raw) async {
    if (state.busy) return;
    final e164 = normalizeUgPhone('+$ugCountryCode$raw');
    if (e164 == null) {
      state = state.copyWith(failure: OtpFailure.invalidPhone);
      return;
    }
    await _request(e164, goToCode: true);
  }

  /// Sends a new code to the same number ("Resend code").
  Future<void> resend() async {
    final e164 = state.e164;
    if (e164 == null || !state.canResend) return;
    await _request(e164, goToCode: false);
  }

  Future<void> _request(String e164, {required bool goToCode}) async {
    state = state.copyWith(e164: e164, busy: true, clearFailure: true);
    try {
      final r = await _auth.requestOtp(e164);
      state = state.copyWith(
        step: OtpStep.code,
        busy: false,
        smsDelayed: false,
        resendSeconds: r.resendAfter.inSeconds,
      );
      _startCountdown();
    } on OtpException catch (e) {
      // A refused first request keeps the user on the phone screen; a refused
      // resend keeps them on the code screen with the old code still valid.
      state = state.copyWith(
        busy: false,
        failure: e.failure,
        retryAfter: e.retryAfter,
        step: goToCode ? OtpStep.phone : OtpStep.code,
      );
      if (e.failure == OtpFailure.rateLimited && e.retryAfter != null) {
        _holdResend(e.retryAfter!.inSeconds);
      }
    }
  }

  void _holdResend(int seconds) {
    state = state.copyWith(resendSeconds: seconds);
    _startCountdown();
  }

  void _startCountdown() {
    _timer?.cancel();
    if (state.resendSeconds <= 0) {
      state = state.copyWith(smsDelayed: true);
      return;
    }
    _timer = Timer.periodic(const Duration(seconds: 1), (t) {
      final left = state.resendSeconds - 1;
      if (left <= 0) {
        t.cancel();
        state = state.copyWith(resendSeconds: 0, smsDelayed: true);
      } else {
        state = state.copyWith(resendSeconds: left);
      }
    });
  }

  /// Called on every edit of the 6 boxes; submits automatically on the 6th digit.
  Future<void> codeChanged(String code) async {
    if (state.failure != null && !state.busy) {
      state = state.copyWith(clearFailure: true);
    }
    if (code.length == 6) await verify(code);
  }

  Future<void> verify(String code) async {
    final e164 = state.e164;
    if (e164 == null || _verifying || code.length != 6) return;
    _verifying = true;
    state = state.copyWith(busy: true, clearFailure: true);
    try {
      final s = await _auth.verifyOtp(e164, code);
      _timer?.cancel();
      state = state.copyWith(busy: false, session: s);
    } on OtpException catch (e) {
      state = state.copyWith(
        busy: false,
        failure: e.failure,
        attemptsLeft: e.attemptsLeft,
        retryAfter: e.retryAfter,
      );
    } finally {
      _verifying = false;
    }
  }

  /// "Use a different number": back to phone entry, previous code abandoned.
  void changeNumber() {
    _timer?.cancel();
    state = const OtpFlowState();
  }
}
