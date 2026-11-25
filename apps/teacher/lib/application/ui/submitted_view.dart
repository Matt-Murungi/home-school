import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_school_shared/home_school_shared.dart';

import '../../l10n/gen/teacher_strings.dart';
import '../state/application_controller.dart';

/// Minimal "submitted" screen. The full T-09 status timeline is V03.
class SubmittedView extends ConsumerWidget {
  const SubmittedView({super.key, required this.onLeave});
  final VoidCallback onLeave;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final s = TeacherStrings.of(context);
    final st = ref.watch(applicationControllerProvider);
    final c = ref.read(applicationControllerProvider.notifier);
    final (title, body) = switch (st.status) {
      'not_approved' => (s.statusNotApprovedTitle, s.statusNotApprovedBody),
      'approved' => (s.statusApprovedTitle, s.statusApprovedBody),
      'under_review' => (s.statusUnderReviewTitle, s.statusSubmittedBody),
      _ => (s.statusSubmittedTitle, s.statusSubmittedBody),
    };
    return Scaffold(
      appBar: AppBar(title: Text(s.applyTitle)),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(HsSpace.lg),
          children: [
            const Icon(
              Icons.check_circle_outline,
              size: 56,
              color: HsColors.success,
            ),
            const SizedBox(height: HsSpace.md),
            Semantics(
              header: true,
              child: Text(
                title,
                style: Theme.of(context).textTheme.headlineSmall,
              ),
            ),
            const SizedBox(height: HsSpace.sm),
            Text(body),
            if (st.status == 'not_approved') ...[
              const SizedBox(height: HsSpace.lg),
              if (st.canReopen)
                FilledButton(onPressed: c.reopen, child: Text(s.reapply))
              else if (st.reopenAt != null)
                Text(
                  s.reapplyOn(
                    st.reopenAt!.toLocal().toString().substring(0, 10),
                  ),
                ),
            ],
            const SizedBox(height: HsSpace.lg),
            OutlinedButton(onPressed: onLeave, child: Text(s.goHome)),
          ],
        ),
      ),
    );
  }
}
