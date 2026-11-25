import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_school_shared/home_school_shared.dart';

import '../../../l10n/gen/teacher_strings.dart';
import '../../model/application_draft.dart';
import '../../state/application_controller.dart';
import '../widgets.dart';

/// T-06 Referees: exactly two, each with name, phone, relationship, years known.
class RefereesStep extends ConsumerWidget {
  const RefereesStep({super.key, required this.issues});
  final List<String> issues;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final s = TeacherStrings.of(context);
    final st = ref.watch(applicationControllerProvider);
    final c = ref.read(applicationControllerProvider.notifier);
    List<RefereeInput> two() => [
      for (var i = 0; i < 2; i++)
        i < st.draft.referees.length
            ? st.draft.referees[i]
            : const RefereeInput(),
    ];
    void setRef(int i, RefereeInput Function(RefereeInput) f) => c.update((d) {
      final l = [
        for (var k = 0; k < 2; k++)
          k < d.referees.length ? d.referees[k] : const RefereeInput(),
      ];
      l[i] = f(l[i]);
      return d.copyWith(referees: l);
    });
    final refs = two();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(s.refereesIntro),
        const SizedBox(height: HsSpace.md),
        for (var i = 0; i < 2; i++) ...[
          SectionTitle(s.refereeN(i + 1)),
          AppField(
            label: s.fieldRefereeName,
            initialValue: refs[i].name ?? '',
            error: issueText(s, issues, 'referees[$i].name'),
            autofillHints: null,
            textCapitalization: TextCapitalization.words,
            onChanged: (v) => setRef(i, (r) => r.copyWith(name: v)),
          ),
          AppField(
            label: s.fieldRefereePhone,
            initialValue: refs[i].phone ?? '',
            error: issueText(s, issues, 'referees[$i].phone'),
            helper: s.refereePhoneHelp,
            keyboardType: TextInputType.phone,
            formatters: [
              FilteringTextInputFormatter.allow(RegExp(r'[0-9 +().\-]')),
            ],
            onChanged: (v) => setRef(i, (r) => r.copyWith(phone: v)),
          ),
          AppDropdown(
            label: s.fieldRelationship,
            value: refs[i].relationship,
            error: issueText(s, issues, 'referees[$i].relationship'),
            options: [
              ('head_teacher', s.relHeadTeacher),
              ('colleague', s.relColleague),
              ('supervisor', s.relSupervisor),
              ('lecturer', s.relLecturer),
              ('employer', s.relEmployer),
              ('community_leader', s.relCommunityLeader),
              ('other', s.relOther),
            ],
            onChanged: (v) => setRef(i, (r) => r.copyWith(relationship: v)),
          ),
          AppField(
            label: s.fieldYearsKnown,
            initialValue: refs[i].yearsKnown?.toString() ?? '',
            error: issueText(s, issues, 'referees[$i].years_known'),
            keyboardType: TextInputType.number,
            formatters: [
              FilteringTextInputFormatter.digitsOnly,
              LengthLimitingTextInputFormatter(2),
            ],
            onChanged: (v) => setRef(
              i,
              (r) => v.isEmpty
                  ? r.copyWith(clearYears: true)
                  : r.copyWith(yearsKnown: int.tryParse(v)),
            ),
          ),
          AppField(
            label: s.fieldRefereeEmail,
            initialValue: refs[i].email ?? '',
            error: issueText(s, issues, 'referees[$i].email'),
            keyboardType: TextInputType.emailAddress,
            onChanged: (v) => setRef(i, (r) => r.copyWith(email: v)),
          ),
        ],
        if (issueText(s, issues, 'referees') != null)
          ErrorLine(issueText(s, issues, 'referees')!),
        CheckboxListTile(
          contentPadding: EdgeInsets.zero,
          controlAffinity: ListTileControlAffinity.leading,
          value: st.draft.refereeConsent,
          title: Text(s.refereeConsent),
          onChanged: (v) =>
              c.update((d) => d.copyWith(refereeConsent: v ?? false)),
        ),
        if (issueText(s, issues, 'referee_consent') != null)
          ErrorLine(issueText(s, issues, 'referee_consent')!),
      ],
    );
  }
}
