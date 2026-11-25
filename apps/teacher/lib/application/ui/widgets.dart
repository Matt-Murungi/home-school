import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:home_school_shared/home_school_shared.dart';

import '../../l10n/gen/teacher_strings.dart';
import '../model/rules.dart';

/// Message for the first issue at exactly [path] (`path:code`), or null.
String? issueText(
  TeacherStrings s,
  Iterable<String> issues,
  String path, {
  int minHours = defaultMinWeeklyHours,
}) {
  for (final i in issues) {
    final at = i.indexOf(':');
    if (at < 0 || i.substring(0, at) != path) continue;
    return switch (i.substring(at + 1)) {
      'required' => s.errRequired,
      'too_short' => s.errTooShort,
      'too_long' => s.errTooLong,
      'invalid' => s.errInvalid,
      'unknown_choice' => s.errUnknownChoice,
      'under_age' => s.errUnderAge,
      'out_of_range' => s.errOutOfRange,
      'duplicate' => s.errDuplicate,
      'own_number' => s.errOwnNumber,
      'overlap' => s.errOverlap,
      'wrong_count' => s.errWrongCount,
      'too_many' => s.errTooMany,
      'too_few_hours' => s.errTooFewHours(minHours),
      _ => s.errInvalid,
    };
  }
  return null;
}

/// Labelled single-line field. 48 dp high at least, wraps at large font scales.
class AppField extends StatelessWidget {
  const AppField({
    super.key,
    required this.label,
    required this.initialValue,
    required this.onChanged,
    this.error,
    this.helper,
    this.keyboardType,
    this.autofillHints,
    this.textCapitalization = TextCapitalization.none,
    this.prefixText,
    this.formatters,
    this.textInputAction = TextInputAction.next,
  });

  final String label;
  final String initialValue;
  final ValueChanged<String> onChanged;
  final String? error;
  final String? helper;
  final TextInputType? keyboardType;
  final Iterable<String>? autofillHints;
  final TextCapitalization textCapitalization;
  final String? prefixText;
  final List<TextInputFormatter>? formatters;
  final TextInputAction textInputAction;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: HsSpace.lg),
    child: TextFormField(
      initialValue: initialValue,
      onChanged: onChanged,
      keyboardType: keyboardType,
      autofillHints: autofillHints,
      textCapitalization: textCapitalization,
      textInputAction: textInputAction,
      inputFormatters: [LengthLimitingTextInputFormatter(500), ...?formatters],
      minLines: 1,
      maxLines: 1,
      decoration: InputDecoration(
        labelText: label,
        errorText: error,
        errorMaxLines: 3,
        helperText: helper,
        helperMaxLines: 3,
        prefixText: prefixText,
        constraints: const BoxConstraints(minHeight: HsSize.minTapTarget),
      ),
    ),
  );
}

class SectionTitle extends StatelessWidget {
  const SectionTitle(this.text, {super.key});
  final String text;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: HsSpace.sm, bottom: HsSpace.sm),
    child: Semantics(
      header: true,
      child: Text(text, style: Theme.of(context).textTheme.titleMedium),
    ),
  );
}

/// Single choice shown as chips (always >= 48 dp tall, wraps).
class ChoiceChips extends StatelessWidget {
  const ChoiceChips({
    super.key,
    required this.options,
    required this.selected,
    required this.onSelected,
    this.error,
  });
  final List<(String value, String label)> options;
  final String? selected;
  final ValueChanged<String> onSelected;
  final String? error;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Wrap(
        spacing: HsSpace.sm,
        runSpacing: HsSpace.sm,
        children: [
          for (final (v, label) in options)
            ChoiceChip(
              label: Text(label),
              selected: selected == v,
              onSelected: (_) => onSelected(v),
              materialTapTargetSize: MaterialTapTargetSize.padded,
              visualDensity: VisualDensity.standard,
            ),
        ],
      ),
      if (error != null) ErrorLine(error!),
    ],
  );
}

class ErrorLine extends StatelessWidget {
  const ErrorLine(this.text, {super.key});
  final String text;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: HsSpace.xs),
    child: Semantics(
      liveRegion: true,
      child: Text(
        text,
        style: Theme.of(context).textTheme.bodySmall
            ?.copyWith(color: HsColors.danger),
      ),
    ),
  );
}

/// Dropdown with a >= 48 dp field.
class AppDropdown extends StatelessWidget {
  const AppDropdown({
    super.key,
    required this.label,
    required this.value,
    required this.options,
    required this.onChanged,
    this.error,
  });
  final String label;
  final String? value;
  final List<(String value, String label)> options;
  final ValueChanged<String?> onChanged;
  final String? error;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: HsSpace.lg),
    child: DropdownButtonFormField<String>(
      initialValue: value,
      isExpanded: true,
      decoration: InputDecoration(
        labelText: label,
        errorText: error,
        errorMaxLines: 3,
        constraints: const BoxConstraints(minHeight: HsSize.minTapTarget),
      ),
      items: [
        for (final (v, l) in options)
          DropdownMenuItem(
            value: v,
            child: Text(l, overflow: TextOverflow.ellipsis),
          ),
      ],
      onChanged: onChanged,
    ),
  );
}
