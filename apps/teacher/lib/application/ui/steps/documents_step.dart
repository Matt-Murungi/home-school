import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_school_shared/home_school_shared.dart';

import '../../../l10n/gen/teacher_strings.dart';

/// Replaced by V02 (document upload). Until then the step explains what will
/// be asked for and lets the teacher carry on: documents are not part of the
/// application payload and do not block submission.
final documentsStepProvider = Provider<WidgetBuilder>(
  (ref) =>
      (context) => const DocumentsPlaceholder(),
);

/// T-05 Documents (upload arrives with V02).
class DocumentsStep extends ConsumerWidget {
  const DocumentsStep({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) =>
      ref.watch(documentsStepProvider)(context);
}

class DocumentsPlaceholder extends StatelessWidget {
  const DocumentsPlaceholder({super.key});

  @override
  Widget build(BuildContext context) {
    final s = TeacherStrings.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(s.documentsIntro),
        const SizedBox(height: HsSpace.md),
        for (final line in [
          s.docNationalId,
          s.docCertificates,
          s.docRegistration,
          s.docPolice,
          s.docCv,
        ])
          ListTile(
            contentPadding: EdgeInsets.zero,
            minTileHeight: HsSize.minTapTarget,
            leading: const Icon(Icons.description_outlined),
            title: Text(line),
          ),
        const SizedBox(height: HsSpace.sm),
        Card(
          color: HsColors.primarySoft,
          child: Padding(
            padding: const EdgeInsets.all(HsSpace.md),
            child: Text(s.documentsLater),
          ),
        ),
      ],
    );
  }
}
