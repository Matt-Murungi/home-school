import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_school_shared/home_school_shared.dart';
import 'package:intl/intl.dart';

import '../../../l10n/gen/teacher_strings.dart';
import '../../model/rules.dart';
import '../../state/application_controller.dart';
import '../photo.dart';
import '../widgets.dart';

/// T-03 Personal details.
class PersonalStep extends ConsumerWidget {
  const PersonalStep({super.key, required this.issues});
  final List<String> issues;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final s = TeacherStrings.of(context);
    final st = ref.watch(applicationControllerProvider);
    final c = ref.read(applicationControllerProvider.notifier);
    final p = st.draft.personal;
    String? e(String f) => issueText(s, issues, 'personal.$f');
    final langName = {
      'en': s.langEnglish,
      'lg': s.langLuganda,
      'sw': s.langSwahili,
      'nyn': s.langRunyankole,
      'xog': s.langLusoga,
      'teo': s.langAteso,
      'ach': s.langAcholi,
      'other': s.langOther,
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AppField(
          label: s.fieldFullName,
          initialValue: p.fullName ?? '',
          error: e('full_name'),
          autofillHints: const [AutofillHints.name],
          textCapitalization: TextCapitalization.words,
          onChanged: (v) => c.update(
            (d) => d.copyWith(personal: d.personal.copyWith(fullName: v)),
          ),
        ),
        SectionTitle(s.fieldTitle),
        ChoiceChips(
          options: [
            ('mr', s.titleMr),
            ('ms', s.titleMs),
            ('teacher', s.titleTeacher),
          ],
          selected: p.title,
          error: e('title'),
          onSelected: (v) => c.update(
            (d) => d.copyWith(personal: d.personal.copyWith(title: v)),
          ),
        ),
        SectionTitle(s.fieldGender),
        ChoiceChips(
          options: [('female', s.genderFemale), ('male', s.genderMale)],
          selected: p.gender,
          error: e('gender'),
          onSelected: (v) => c.update(
            (d) => d.copyWith(personal: d.personal.copyWith(gender: v)),
          ),
        ),
        const SizedBox(height: HsSpace.lg),
        _DobField(
          value: p.dateOfBirth,
          error: e('date_of_birth'),
          onChanged: (v) => c.update(
            (d) => d.copyWith(personal: d.personal.copyWith(dateOfBirth: v)),
          ),
        ),
        SectionTitle(s.fieldLanguages),
        Wrap(
          spacing: HsSpace.sm,
          runSpacing: HsSpace.sm,
          children: [
            for (final code in languageCodes)
              FilterChip(
                label: Text(langName[code]!),
                selected: (p.languages ?? const []).contains(code),
                materialTapTargetSize: MaterialTapTargetSize.padded,
                onSelected: (on) => c.update((d) {
                  final cur = [...?d.personal.languages];
                  on ? cur.add(code) : cur.remove(code);
                  return d.copyWith(
                    personal: d.personal.copyWith(languages: cur),
                  );
                }),
              ),
          ],
        ),
        if (e('languages') != null) ErrorLine(e('languages')!),
        const SizedBox(height: HsSpace.lg),
        PhotoSection(
          photo: p.photo,
          error: e('photo'),
          onChanged: (photo) => c.update(
            (d) => d.copyWith(
              personal: photo == null
                  ? d.personal.copyWith(clearPhoto: true)
                  : d.personal.copyWith(photo: photo),
            ),
          ),
        ),
      ],
    );
  }
}

class _DobField extends StatelessWidget {
  const _DobField({
    required this.value,
    required this.error,
    required this.onChanged,
  });
  final String? value;
  final String? error;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    final s = TeacherStrings.of(context);
    final d = parseIsoDate(value);
    final shown = d == null ? '' : DateFormat.yMMMMd().format(d);
    return Semantics(
      button: true,
      label: s.fieldDob,
      child: InkWell(
        onTap: () async {
          final today = DateTime.now();
          final picked = await showDatePicker(
            context: context,
            initialDate: d ?? DateTime(today.year - 30, 1, 1),
            firstDate: DateTime(today.year - 90),
            lastDate: today,
            initialEntryMode: DatePickerEntryMode.input,
            helpText: s.fieldDob,
          );
          if (picked != null) {
            onChanged(
              '${picked.year.toString().padLeft(4, '0')}-'
              '${picked.month.toString().padLeft(2, '0')}-'
              '${picked.day.toString().padLeft(2, '0')}',
            );
          }
        },
        child: InputDecorator(
          decoration: InputDecoration(
            labelText: s.fieldDob,
            errorText: error,
            errorMaxLines: 3,
            helperText: s.dobHelp,
            helperMaxLines: 2,
            suffixIcon: const Icon(Icons.calendar_month_outlined),
            constraints: const BoxConstraints(minHeight: HsSize.minTapTarget),
          ),
          child: Text(shown.isEmpty ? s.dobPlaceholder : shown),
        ),
      ),
    );
  }
}
