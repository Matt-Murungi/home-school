import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_school_shared/home_school_shared.dart';

import '../../../l10n/gen/teacher_strings.dart';
import '../../model/application_draft.dart';
import '../../model/reference.dart';
import '../../state/application_controller.dart';
import '../widgets.dart';

/// T-04 Qualifications and curricula.
class QualificationsStep extends ConsumerWidget {
  const QualificationsStep({super.key, required this.issues});
  final List<String> issues;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final s = TeacherStrings.of(context);
    final st = ref.watch(applicationControllerProvider);
    final c = ref.read(applicationControllerProvider.notifier);
    final q = st.draft.qualifications;
    String? e(String f) => issueText(s, issues, 'qualifications.$f');
    void setQ(Qualifications Function(Qualifications) f) =>
        c.update((d) => d.copyWith(qualifications: f(d.qualifications)));
    final registered = q.teacherType == 'registered';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SectionTitle(s.fieldTeacherType),
        ChoiceChips(
          options: [
            ('registered', s.typeRegistered),
            ('graduate', s.typeGraduate),
          ],
          selected: q.teacherType,
          error: e('teacher_type'),
          onSelected: (v) => setQ((x) => x.copyWith(teacherType: v)),
        ),
        const SizedBox(height: HsSpace.lg),
        AppField(
          key: ValueKey('reg-${st.generation}-${q.teacherType}'),
          label: registered ? s.fieldRegistration : s.fieldRegistrationOptional,
          initialValue: q.registrationNumber ?? '',
          error: e('registration_number'),
          helper: registered ? null : s.registrationGraduateHelp,
          textCapitalization: TextCapitalization.characters,
          onChanged: (v) => setQ(
            (x) => v.isEmpty
                ? x.copyWith(clearRegistration: true)
                : x.copyWith(registrationNumber: v),
          ),
        ),
        AppDropdown(
          label: s.fieldHighestQualification,
          value: q.highestQualification,
          error: e('highest_qualification'),
          options: [
            ('certificate', s.qualCertificate),
            ('diploma', s.qualDiploma),
            ('degree', s.qualDegree),
            ('postgraduate', s.qualPostgraduate),
            ('masters', s.qualMasters),
            ('doctorate', s.qualDoctorate),
          ],
          onChanged: (v) => setQ((x) => x.copyWith(highestQualification: v)),
        ),
        AppField(
          label: s.fieldInstitution,
          initialValue: q.institution ?? '',
          error: e('institution'),
          textCapitalization: TextCapitalization.words,
          onChanged: (v) => setQ((x) => x.copyWith(institution: v)),
        ),
        AppField(
          label: s.fieldYearsTeaching,
          initialValue: q.yearsTeaching?.toString() ?? '',
          error: e('years_teaching'),
          keyboardType: TextInputType.number,
          formatters: [
            FilteringTextInputFormatter.digitsOnly,
            LengthLimitingTextInputFormatter(3),
          ],
          onChanged: (v) => setQ(
            (x) => v.isEmpty
                ? x.copyWith(clearYears: true)
                : x.copyWith(yearsTeaching: int.tryParse(v)),
          ),
        ),
        AppField(
          label: s.fieldCurrentSchool,
          initialValue: q.currentSchool ?? '',
          error: e('current_school'),
          helper: s.currentSchoolHelp,
          textCapitalization: TextCapitalization.words,
          onChanged: (v) => setQ((x) => x.copyWith(currentSchool: v)),
        ),
        const Divider(),
        SectionTitle(s.curriculaTitle),
        Text(s.curriculaHelp),
        const SizedBox(height: HsSpace.md),
        _Curricula(issues: issues),
      ],
    );
  }
}

class _Curricula extends ConsumerWidget {
  const _Curricula({required this.issues});
  final List<String> issues;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final s = TeacherStrings.of(context);
    final st = ref.watch(applicationControllerProvider);
    final c = ref.read(applicationControllerProvider.notifier);
    final reference = st.reference;
    if (reference == null) {
      if (st.referenceFailed) {
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(s.catalogueUnavailable),
            const SizedBox(height: HsSpace.sm),
            OutlinedButton(
              onPressed: c.retryReference,
              child: Text(s.tryAgain),
            ),
          ],
        );
      }
      return Semantics(
        label: s.loadingCatalogue,
        child: const Center(
          child: Padding(
            padding: EdgeInsets.all(HsSpace.lg),
            child: CircularProgressIndicator(),
          ),
        ),
      );
    }
    final chosen = {for (final x in st.draft.curricula) x.curriculumId: x};
    void setList(List<CurriculumChoice> l) =>
        c.update((d) => d.copyWith(curricula: l));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (issueText(s, issues, 'curricula') != null)
          ErrorLine(issueText(s, issues, 'curricula')!),
        for (final cur in reference.curricula)
          Card(
            child: Padding(
              padding: const EdgeInsets.symmetric(
                horizontal: HsSpace.md,
                vertical: HsSpace.xs,
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(cur.name),
                    value: chosen.containsKey(cur.id),
                    onChanged: (on) {
                      final list = [...st.draft.curricula];
                      if (on == true) {
                        list.add(CurriculumChoice(curriculumId: cur.id));
                      } else {
                        list.removeWhere((x) => x.curriculumId == cur.id);
                      }
                      setList(list);
                    },
                  ),
                  if (chosen[cur.id] case final pick?) ...[
                    _Picker(
                      title: s.levelsTitle,
                      options: cur.levels,
                      selected: pick.levelIds,
                      error: _curErr(
                        s,
                        issues,
                        st.draft.curricula,
                        cur.id,
                        'level_ids',
                      ),
                      onChanged: (ids) => setList([
                        for (final x in st.draft.curricula)
                          x.curriculumId == cur.id
                              ? x.copyWith(levelIds: ids)
                              : x,
                      ]),
                    ),
                    _Picker(
                      title: s.subjectsTitle,
                      options: cur.subjects,
                      selected: pick.subjectIds,
                      error: _curErr(
                        s,
                        issues,
                        st.draft.curricula,
                        cur.id,
                        'subject_ids',
                      ),
                      onChanged: (ids) => setList([
                        for (final x in st.draft.curricula)
                          x.curriculumId == cur.id
                              ? x.copyWith(subjectIds: ids)
                              : x,
                      ]),
                    ),
                    const SizedBox(height: HsSpace.sm),
                  ],
                ],
              ),
            ),
          ),
      ],
    );
  }

  String? _curErr(
    TeacherStrings s,
    List<String> issues,
    List<CurriculumChoice> list,
    String id,
    String field,
  ) {
    final i = list.indexWhere((x) => x.curriculumId == id);
    return i < 0 ? null : issueText(s, issues, 'curricula[$i].$field');
  }
}

class _Picker extends StatelessWidget {
  const _Picker({
    required this.title,
    required this.options,
    required this.selected,
    required this.onChanged,
    this.error,
  });
  final String title;
  final List<NamedId> options;
  final List<String> selected;
  final ValueChanged<List<String>> onChanged;
  final String? error;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Padding(
        padding: const EdgeInsets.only(top: HsSpace.sm, bottom: HsSpace.xs),
        child: Text(title, style: Theme.of(context).textTheme.titleSmall),
      ),
      Wrap(
        spacing: HsSpace.sm,
        runSpacing: HsSpace.xs,
        children: [
          for (final o in options)
            FilterChip(
              label: Text(o.name),
              selected: selected.contains(o.id),
              materialTapTargetSize: MaterialTapTargetSize.padded,
              onSelected: (on) => onChanged(
                on
                    ? [...selected, o.id]
                    : [
                        for (final x in selected)
                          if (x != o.id) x,
                      ],
              ),
            ),
        ],
      ),
      if (error != null) ErrorLine(error!),
    ],
  );
}
