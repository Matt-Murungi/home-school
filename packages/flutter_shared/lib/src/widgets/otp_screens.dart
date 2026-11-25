import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/otp_controller.dart';
import '../auth/otp_models.dart';
import '../auth/phone.dart';
import '../l10n/gen/hs_strings.dart';
import '../theme/tokens.dart';

String _duration(HsStrings s, Duration? d) {
  final secs = (d ?? const Duration(minutes: 1)).inSeconds.clamp(1, 86400);
  if (secs < 60) return s.otpDurationSeconds(secs);
  return s.otpDurationMinutes((secs / 60).ceil());
}

/// The user-facing sentence for a failed step. Null failure -> null.
String? otpFailureMessage(HsStrings s, OtpFlowState st) {
  final f = st.failure;
  if (f == null) return null;
  return switch (f) {
    OtpFailure.invalidPhone => s.otpInvalidPhone,
    OtpFailure.wrongCode =>
      st.attemptsLeft == null
          ? s.otpWrongCodeNoCount
          : s.otpWrongCode(st.attemptsLeft!),
    OtpFailure.expired => s.otpExpired,
    OtpFailure.tooManyAttempts => s.otpTooManyAttempts,
    OtpFailure.rateLimited => s.otpRateLimited(_duration(s, st.retryAfter)),
    OtpFailure.locked => s.otpLocked(_duration(s, st.retryAfter)),
    OtpFailure.offline => s.otpOffline,
    OtpFailure.smsUnavailable => s.otpSmsUnavailable,
    OtpFailure.numberUnreachable => s.otpNumberUnreachable,
    OtpFailure.accountBlocked => s.otpAccountBlocked,
    OtpFailure.suspended => s.suspendedTitle,
    OtpFailure.staffAccount => s.otpStaffAccount,
    OtpFailure.roleRevoked => s.otpRoleRevoked,
    OtpFailure.serverUnavailable => s.otpServerUnavailable,
    OtpFailure.unknown => s.otpUnknownError,
  };
}

class _ErrorText extends StatelessWidget {
  const _ErrorText(this.message);
  final String message;

  @override
  Widget build(BuildContext context) => Semantics(
    liveRegion: true,
    container: true,
    child: Padding(
      padding: const EdgeInsets.only(top: HsSpace.sm),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const ExcludeSemantics(
            child: Icon(Icons.error_outline, color: HsColors.danger, size: 20),
          ),
          const SizedBox(width: HsSpace.sm),
          Expanded(
            child: Text(
              message,
              key: const Key('otp-error'),
              style: Theme.of(context).textTheme.bodyMedium
                  ?.copyWith(color: HsColors.danger),
            ),
          ),
        ],
      ),
    ),
  );
}

/// P-02 / T-02 phone number: fixed +256, nine-digit field, "Send code".
/// [onCodeSent] runs once the code is on its way (navigate to the code screen).
class PhoneEntryScreen extends ConsumerStatefulWidget {
  const PhoneEntryScreen({super.key, required this.onCodeSent});
  final VoidCallback onCodeSent;

  @override
  ConsumerState<PhoneEntryScreen> createState() => _PhoneEntryScreenState();
}

class _PhoneEntryScreenState extends ConsumerState<PhoneEntryScreen> {
  final _controller = TextEditingController();

  @override
  void initState() {
    super.initState();
    // A fresh visit starts a clean flow (after the first frame: providers must
    // not change while the tree is building).
    Future.microtask(() {
      if (mounted) ref.read(otpFlowProvider.notifier).reset();
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final flow = ref.read(otpFlowProvider.notifier);
    await flow.submitPhone(
      UgPhoneInputFormatter.nationalDigits(_controller.text),
    );
  }

  @override
  Widget build(BuildContext context) {
    final s = HsStrings.of(context);
    final theme = Theme.of(context);
    final st = ref.watch(otpFlowProvider);
    ref.listen(otpFlowProvider, (prev, next) {
      if (prev?.step != OtpStep.code && next.step == OtpStep.code) {
        widget.onCodeSent();
      }
    });
    final error = otpFailureMessage(s, st);
    return Scaffold(
      appBar: AppBar(title: Text(s.appName)),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(HsSpace.xl),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Semantics(
                header: true,
                child: Text(s.otpPhoneTitle, style: theme.textTheme.titleLarge),
              ),
              const SizedBox(height: HsSpace.sm),
              Text(s.otpPhoneNote, style: theme.textTheme.bodyLarge),
              const SizedBox(height: HsSpace.xl),
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  ExcludeSemantics(
                    child: Container(
                      key: const Key('otp-country-code'),
                      constraints: const BoxConstraints(
                        minHeight: HsSize.minTapTarget,
                        minWidth: HsSize.minTapTarget,
                      ),
                      padding: const EdgeInsets.symmetric(
                        horizontal: HsSpace.md,
                      ),
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        color: HsColors.primarySoft,
                        borderRadius: BorderRadius.circular(HsRadius.button),
                      ),
                      child: Text(
                        s.otpCountryCode,
                        style: theme.textTheme.titleMedium,
                      ),
                    ),
                  ),
                  const SizedBox(width: HsSpace.sm),
                  Expanded(
                    child: Semantics(
                      label: s.otpPhoneFieldSemantics,
                      textField: true,
                      child: TextField(
                        key: const Key('otp-phone-field'),
                        controller: _controller,
                        enabled: !st.busy,
                        keyboardType: TextInputType.phone,
                        textInputAction: TextInputAction.done,
                        autofillHints: const [
                          AutofillHints.telephoneNumberNational,
                        ],
                        inputFormatters: const [UgPhoneInputFormatter()],
                        onChanged: (_) =>
                            ref.read(otpFlowProvider.notifier).clearError(),
                        onSubmitted: (_) => _submit(),
                        decoration: InputDecoration(
                          labelText: s.otpPhoneLabel,
                          hintText: s.otpPhoneHint,
                          errorText: null,
                          border: const OutlineInputBorder(),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
              if (error != null) _ErrorText(error),
              const SizedBox(height: HsSpace.xl),
              FilledButton(
                key: const Key('otp-send'),
                onPressed: st.busy ? null : _submit,
                child: st.busy
                    ? Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          const SizedBox(
                            width: 20,
                            height: 20,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          ),
                          const SizedBox(width: HsSpace.sm),
                          Flexible(child: Text(s.otpSending)),
                        ],
                      )
                    : Text(s.otpSendCode),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// P-03 verify code: six boxes, auto-submit on the sixth digit, resend
/// countdown, "use a different number", help for delayed SMS.
class CodeEntryScreen extends ConsumerStatefulWidget {
  const CodeEntryScreen({
    super.key,
    required this.onSignedIn,
    required this.onChangeNumber,
    this.onSuspended,
  });
  final VoidCallback onSignedIn;
  final VoidCallback onChangeNumber;

  /// A03: the account is suspended (teacher app: a suspended teacher). Shown
  /// as its own state, not as a sign-in error.
  final VoidCallback? onSuspended;

  @override
  ConsumerState<CodeEntryScreen> createState() => _CodeEntryScreenState();
}

class _CodeEntryScreenState extends ConsumerState<CodeEntryScreen> {
  final _controller = TextEditingController();
  final _focus = FocusNode();

  @override
  void dispose() {
    _controller.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _clearBoxes() {
    _controller.clear();
    _focus.requestFocus();
  }

  void _showHelp(HsStrings s) {
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (c) => SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(HsSpace.xl),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(s.otpHelpTitle, style: Theme.of(c).textTheme.titleLarge),
              const SizedBox(height: HsSpace.md),
              Text(s.otpHelpBody, style: Theme.of(c).textTheme.bodyLarge),
              const SizedBox(height: HsSpace.xl),
              FilledButton(
                onPressed: () => Navigator.of(c).pop(),
                child: Text(s.otpClose),
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final s = HsStrings.of(context);
    final theme = Theme.of(context);
    final st = ref.watch(otpFlowProvider);
    final flow = ref.read(otpFlowProvider.notifier);
    ref.listen(otpFlowProvider, (prev, next) {
      if (prev?.session == null && next.session != null) widget.onSignedIn();
      if (prev?.failure != OtpFailure.suspended &&
          next.failure == OtpFailure.suspended) {
        widget.onSuspended?.call();
      }
      if (prev?.failure == null &&
          next.failure != null &&
          next.step == OtpStep.code &&
          (next.failure == OtpFailure.wrongCode ||
              next.failure == OtpFailure.expired ||
              next.failure == OtpFailure.tooManyAttempts)) {
        _clearBoxes();
      }
    });
    final error = otpFailureMessage(s, st);
    final needsNewCode =
        st.failure == OtpFailure.expired ||
        st.failure == OtpFailure.tooManyAttempts;
    final phone = st.e164 == null ? '' : maskPhone(st.e164!);
    return Scaffold(
      appBar: AppBar(
        title: Text(s.appName),
        leading: BackButton(
          onPressed: () {
            flow.changeNumber();
            widget.onChangeNumber();
          },
        ),
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(HsSpace.xl),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Semantics(
                header: true,
                child: Text(s.otpCodeTitle, style: theme.textTheme.titleLarge),
              ),
              const SizedBox(height: HsSpace.sm),
              Text(
                s.otpCodeSentTo(phone),
                key: const Key('otp-sent-to'),
                style: theme.textTheme.bodyLarge,
              ),
              const SizedBox(height: HsSpace.xl),
              _CodeBoxes(
                controller: _controller,
                focus: _focus,
                enabled: !st.busy && !needsNewCode,
                hasError: st.failure != null,
                onChanged: flow.codeChanged,
              ),
              if (st.busy)
                Padding(
                  padding: const EdgeInsets.only(top: HsSpace.md),
                  child: Semantics(
                    liveRegion: true,
                    label: s.otpVerifying,
                    child: const LinearProgressIndicator(),
                  ),
                ),
              if (error != null) _ErrorText(error),
              const SizedBox(height: HsSpace.lg),
              ListenableBuilder(
                listenable: _controller,
                builder: (context, _) => FilledButton(
                  key: const Key('otp-verify'),
                  onPressed:
                      st.busy || needsNewCode || _controller.text.length != 6
                      ? null
                      : () => flow.verify(_controller.text),
                  child: Text(s.otpVerify),
                ),
              ),
              const SizedBox(height: HsSpace.sm),
              TextButton(
                key: const Key('otp-resend'),
                onPressed: st.canResend ? flow.resend : null,
                style: TextButton.styleFrom(
                  minimumSize: const Size(
                    HsSize.minTapTarget,
                    HsSize.minTapTarget,
                  ),
                ),
                child: Text(
                  st.resendSeconds > 0
                      ? s.otpResendIn(st.resendSeconds)
                      : s.otpResend,
                ),
              ),
              if (st.smsDelayed && st.failure == null)
                Semantics(
                  liveRegion: true,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: HsSpace.sm),
                    child: Text(
                      s.otpSmsDelayed,
                      key: const Key('otp-sms-delayed'),
                      style: theme.textTheme.bodyMedium,
                    ),
                  ),
                ),
              TextButton(
                key: const Key('otp-change-number'),
                onPressed: () {
                  flow.changeNumber();
                  widget.onChangeNumber();
                },
                style: TextButton.styleFrom(
                  minimumSize: const Size(
                    HsSize.minTapTarget,
                    HsSize.minTapTarget,
                  ),
                ),
                child: Text(s.otpChangeNumber),
              ),
              TextButton(
                key: const Key('otp-help'),
                onPressed: () => _showHelp(s),
                style: TextButton.styleFrom(
                  minimumSize: const Size(
                    HsSize.minTapTarget,
                    HsSize.minTapTarget,
                  ),
                ),
                child: Text(s.otpHelp),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Six visual boxes over one real text field: one focus target, one
/// accessibility node (label "Verification code, 6 digits"), and the OS can
/// auto-fill an SMS code into it (`AutofillHints.oneTimeCode`).
class _CodeBoxes extends StatelessWidget {
  const _CodeBoxes({
    required this.controller,
    required this.focus,
    required this.enabled,
    required this.hasError,
    required this.onChanged,
  });
  final TextEditingController controller;
  final FocusNode focus;
  final bool enabled;
  final bool hasError;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    final s = HsStrings.of(context);
    final theme = Theme.of(context);
    return Semantics(
      label: s.otpCodeFieldSemantics,
      textField: true,
      child: Stack(
        alignment: Alignment.center,
        children: [
          ExcludeSemantics(
            child: ListenableBuilder(
              listenable: controller,
              builder: (context, _) {
                final text = controller.text;
                return Row(
                  children: [
                    for (var i = 0; i < 6; i++) ...[
                      if (i > 0) const SizedBox(width: HsSpace.sm),
                      Expanded(
                        child: Container(
                          key: Key('otp-box-$i'),
                          constraints: const BoxConstraints(
                            minHeight: HsSize.minTapTarget + 8,
                          ),
                          alignment: Alignment.center,
                          decoration: BoxDecoration(
                            color: HsColors.surface,
                            borderRadius: BorderRadius.circular(
                              HsRadius.button,
                            ),
                            border: Border.all(
                              width: i == text.length && enabled ? 2 : 1,
                              color: hasError
                                  ? HsColors.danger
                                  : i == text.length && enabled
                                  ? HsColors.primary
                                  : HsColors.border,
                            ),
                          ),
                          child: Text(
                            i < text.length ? text[i] : '',
                            style: theme.textTheme.titleLarge,
                          ),
                        ),
                      ),
                    ],
                  ],
                );
              },
            ),
          ),
          // The real field: invisible, full width, same height as the boxes.
          Positioned.fill(
            child: TextField(
              key: const Key('otp-code-field'),
              controller: controller,
              focusNode: focus,
              enabled: enabled,
              autofocus: true,
              maxLength: 6,
              keyboardType: TextInputType.number,
              textInputAction: TextInputAction.done,
              autofillHints: const [AutofillHints.oneTimeCode],
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              showCursor: false,
              enableInteractiveSelection: false,
              style: const TextStyle(color: Colors.transparent),
              cursorColor: Colors.transparent,
              decoration: const InputDecoration(
                counterText: '',
                border: InputBorder.none,
                fillColor: Colors.transparent,
              ),
              onChanged: onChanged,
            ),
          ),
        ],
      ),
    );
  }
}
