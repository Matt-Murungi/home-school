import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_school_shared/home_school_shared.dart';

import '../../../l10n/gen/teacher_strings.dart';
import '../../model/rules.dart';
import '../../state/application_controller.dart';
import '../widgets.dart';

/// T-08 Review and submit.
class ReviewStep extends ConsumerStatefulWidget {
  const ReviewStep({super.key, required this.onEdit});
  final ValueChanged<WizardStep> onEdit;

  @override
  ConsumerState<ReviewStep> createState() => _ReviewStepState();
}

class _ReviewStepState extends ConsumerState<ReviewStep> {
  bool declared = false;

  @override
  Widget build(BuildContext context) {
    final s = TeacherStrings.of(context);
    final st = ref.watch(applicationControllerProvider);
    final c = ref.read(applicationControllerProvider.notifier);
    final d = st.draft;
    final all = c.issues(final_: true);
    bool ok(WizardStep step) => !all.any((i) => stepOfIssue(i) == step);
    String names(Iterable<String> ids, List<(String, String)> from) => ids
        .map((i) => from.firstWhere((e) => e.$1 == i, orElse: () => (i, i)).$2)
        .join(', ');
    final cur = st.reference?.curricula ?? const [];
    final zoneNames = names(d.zoneIds, [
      for (final z in st.reference?.zones ?? const []) (z.id, z.name),
    ]);
    final submitting = st.submit == SubmitPhase.submitting;
    final blocked = !st.canEdit;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(s.reviewIntro),
        const SizedBox(height: HsSpace.md),
        _Section(
          title: s.stepPersonal,
          ok: ok(WizardStep.personal),
          onEdit: () => widget.onEdit(WizardStep.personal),
          lines: [
            d.personal.fullName ?? '-',
            if (d.personal.dateOfBirth != null) d.personal.dateOfBirth!,
            if (d.personal.photo != null) s.photoAdded else s.photoMissing,
          ],
        ),
        _Section(
          title: s.stepQualifications,
          ok: ok(WizardStep.qualifications),
          onEdit: () => widget.onEdit(WizardStep.qualifications),
          lines: [
            if (d.qualifications.teacherType == 'registered')
              s.typeRegistered
            else if (d.qualifications.teacherType == 'graduate')
              s.typeGraduate
            else
              '-',
            d.qualifications.institution ?? '-',
            for (final x in d.curricula)
              '${cur.where((k) => k.id == x.curriculumId).map((k) => k.name).firstOrNull ?? x.curriculumId}: '
                  '${s.levelsAndSubjects(x.levelIds.length, x.subjectIds.length)}',
          ],
        ),
        _Section(
          title: s.stepDocuments,
          ok: true,
          onEdit: () => widget.onEdit(WizardStep.documents),
          lines: [s.documentsReviewLine],
        ),
        _Section(
          title: s.stepReferees,
          ok: ok(WizardStep.referees),
          onEdit: () => widget.onEdit(WizardStep.referees),
          lines: [for (final r in d.referees) r.name ?? '-'],
        ),
        _Section(
          title: s.stepZones,
          ok: ok(WizardStep.zones),
          onEdit: () => widget.onEdit(WizardStep.zones),
          lines: [
            zoneNames.isEmpty ? '-' : zoneNames,
            s.availabilityTotal(weeklyMinutes(d.availability) ~/ 60),
          ],
        ),
        const SizedBox(height: HsSpace.md),
        if (all.isNotEmpty) ...[
          Semantics(
            liveRegion: true,
            child: Text(
              s.reviewIncomplete(_missingSections(all)),
              style: const TextStyle(color: HsColors.danger),
            ),
          ),
          const SizedBox(height: HsSpace.sm),
        ] else
          Text(
            s.reviewComplete,
            style: const TextStyle(color: HsColors.success),
          ),
        CheckboxListTile(
          contentPadding: EdgeInsets.zero,
          controlAffinity: ListTileControlAffinity.leading,
          title: Text(s.declaration),
          value: declared,
          onChanged: blocked
              ? null
              : (v) => setState(() => declared = v ?? false),
        ),
        if (st.submit == SubmitPhase.failed && st.submitIssues.isNotEmpty) ...[
          Text(
            s.submitFailedIssues,
            style: const TextStyle(color: HsColors.danger),
          ),
          for (final i in st.submitIssues) Text(_bullet(_issueLabel(s, i))),
        ],
        if (st.submit == SubmitPhase.inProgress)
          Card(
            color: HsColors.primarySoft,
            child: Padding(
              padding: const EdgeInsets.all(HsSpace.md),
              child: Text(s.submitInProgress),
            ),
          ),
        if (st.submit == SubmitPhase.queuedOffline)
          Card(
            color: HsColors.primarySoft,
            child: Padding(
              padding: const EdgeInsets.all(HsSpace.md),
              child: Text(s.submitQueued),
            ),
          ),
        const SizedBox(height: HsSpace.md),
        SizedBox(
          width: double.infinity,
          child: FilledButton(
            onPressed:
                (submitting ||
                    blocked ||
                    st.submit == SubmitPhase.queuedOffline)
                ? null
                : () {
                    if (!declared) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(content: Text(s.declarationNeeded)),
                      );
                      return;
                    }
                    c.submit();
                  },
            child: submitting
                ? Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      ),
                      const SizedBox(width: HsSpace.sm),
                      Text(s.submitting),
                    ],
                  )
                : Text(s.submitApplication),
          ),
        ),
      ],
    );
  }

  static String _bullet(String text) => '\u2022 $text';

  String _missingSections(List<String> all) {
    final s = TeacherStrings.of(context);
    final steps = {for (final i in all) stepOfIssue(i)};
    String n(WizardStep x) => switch (x) {
      WizardStep.personal => s.stepPersonal,
      WizardStep.qualifications => s.stepQualifications,
      WizardStep.referees => s.stepReferees,
      WizardStep.zones => s.stepZones,
      _ => '',
    };
    return steps.map(n).where((x) => x.isNotEmpty).join(', ');
  }

  String _issueLabel(TeacherStrings s, String issue) {
    final path = issue.split(':').first;
    return '${_where(s, path)}: ${issueText(s, [issue], path) ?? issue}';
  }

  String _where(TeacherStrings s, String path) => switch (stepOfIssue(path)) {
    WizardStep.personal => s.stepPersonal,
    WizardStep.qualifications => s.stepQualifications,
    WizardStep.referees => s.stepReferees,
    _ => s.stepZones,
  };
}

class _Section extends StatelessWidget {
  const _Section({
    required this.title,
    required this.ok,
    required this.lines,
    required this.onEdit,
  });
  final String title;
  final bool ok;
  final List<String> lines;
  final VoidCallback onEdit;

  @override
  Widget build(BuildContext context) {
    final s = TeacherStrings.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(HsSpace.md),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Semantics(
              label: ok ? s.sectionDone : s.sectionNeedsWork,
              child: Icon(
                ok ? Icons.check_circle : Icons.error_outline,
                color: ok ? HsColors.success : HsColors.danger,
              ),
            ),
            const SizedBox(width: HsSpace.md),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: Theme.of(context).textTheme.titleSmall),
                  for (final l in lines) Text(l),
                ],
              ),
            ),
            TextButton(
              onPressed: onEdit,
              style: TextButton.styleFrom(
                minimumSize: const Size(
                  HsSize.minTapTarget,
                  HsSize.minTapTarget,
                ),
              ),
              child: Text(s.edit),
            ),
          ],
        ),
      ),
    );
  }
}
