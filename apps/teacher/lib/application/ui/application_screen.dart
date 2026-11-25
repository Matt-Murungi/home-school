import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:home_school_shared/home_school_shared.dart';

import '../../l10n/gen/teacher_strings.dart';
import '../model/rules.dart';
import '../state/application_controller.dart';
import 'steps/documents_step.dart';
import 'steps/personal_step.dart';
import 'steps/qualifications_step.dart';
import 'steps/referees_step.dart';
import 'steps/review_step.dart';
import 'steps/zones_step.dart';
import 'submitted_view.dart';

const applyPath = '/apply';

/// The application wizard (T-03 .. T-08). Hosts the progress header, the
/// saved/offline banner and back/next navigation.
class ApplicationScreen extends ConsumerStatefulWidget {
  const ApplicationScreen({super.key, this.onLeave});

  /// Called when the teacher leaves the flow (after submit, "go home").
  final VoidCallback? onLeave;

  @override
  ConsumerState<ApplicationScreen> createState() => _ApplicationScreenState();
}

class _ApplicationScreenState extends ConsumerState<ApplicationScreen>
    with WidgetsBindingObserver {
  final Set<int> _attempted = {};

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    Future.microtask(
      () => ref.read(applicationControllerProvider.notifier).load(),
    );
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final c = ref.read(applicationControllerProvider.notifier);
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.inactive) {
      c.flush(); // push edits as the app goes to the background
    } else if (state == AppLifecycleState.resumed) {
      c.retryNow();
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = TeacherStrings.of(context);
    final st = ref.watch(applicationControllerProvider);
    final c = ref.read(applicationControllerProvider.notifier);
    if (st.loading) {
      return Scaffold(
        appBar: AppBar(title: Text(s.applyTitle)),
        body: Center(
          child: Semantics(
            label: s.loading,
            child: const CircularProgressIndicator(),
          ),
        ),
      );
    }
    if (st.loadFailed) {
      return Scaffold(
        appBar: AppBar(title: Text(s.applyTitle)),
        body: Padding(
          padding: const EdgeInsets.all(HsSpace.lg),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(s.loadFailed, textAlign: TextAlign.center),
              const SizedBox(height: HsSpace.lg),
              FilledButton(onPressed: c.load, child: Text(s.tryAgain)),
            ],
          ),
        ),
      );
    }
    if (st.submitted || st.submit == SubmitPhase.done) {
      return SubmittedView(
        onLeave: widget.onLeave ?? () => context.go('/today'),
      );
    }
    final steps = WizardStep.values;
    final index = st.step.clamp(0, steps.length - 1);
    final step = steps[index];
    final all = c.issues(final_: true);
    final draftIssues = c.issues(final_: false);
    final shown = {
      ...draftIssues,
      if (_attempted.contains(index) || st.visited.contains(index))
        ...all.where((i) => stepOfIssue(i) == step),
      if (step == WizardStep.review) ...st.submitIssues,
    }.toList();
    final stepTitle = _title(s, step);
    return PopScope(
      canPop: index == 0,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && index > 0) c.goToStep(index - 1);
      },
      child: Scaffold(
        appBar: AppBar(title: Text(s.applyTitle)),
        body: SafeArea(
          child: Column(
            children: [
              _Header(index: index, total: steps.length, title: stepTitle),
              _SyncBanner(state: st, onRetry: c.retryNow),
              if (st.conflict != null)
                _ConflictCard(
                  onKeepMine: () => c.resolveConflict(keepMine: true),
                  onUseTheirs: () => c.resolveConflict(keepMine: false),
                ),
              Expanded(
                child: ListView(
                  key: PageStorageKey('step-$index-${st.generation}'),
                  padding: const EdgeInsets.fromLTRB(
                    HsSpace.lg,
                    HsSpace.sm,
                    HsSpace.lg,
                    HsSpace.lg,
                  ),
                  children: [
                    KeyedSubtree(
                      key: ValueKey('gen-${st.generation}-$index'),
                      child: _body(step, shown, c),
                    ),
                  ],
                ),
              ),
              _NavBar(
                index: index,
                last: steps.length - 1,
                onBack: () => c.goToStep(index - 1),
                onNext: () {
                  final mine = all.where((i) => stepOfIssue(i) == step);
                  if (mine.isNotEmpty) {
                    setState(() => _attempted.add(index));
                    return;
                  }
                  c.goToStep(index + 1);
                },
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _body(WizardStep step, List<String> shown, ApplicationController c) =>
      switch (step) {
        WizardStep.personal => PersonalStep(issues: shown),
        WizardStep.qualifications => QualificationsStep(issues: shown),
        WizardStep.documents => const DocumentsStep(),
        WizardStep.referees => RefereesStep(issues: shown),
        WizardStep.zones => ZonesStep(issues: shown),
        WizardStep.review => ReviewStep(onEdit: (x) => c.goToStep(x.index)),
      };

  static String _title(TeacherStrings s, WizardStep step) => switch (step) {
    WizardStep.personal => s.stepPersonal,
    WizardStep.qualifications => s.stepQualifications,
    WizardStep.documents => s.stepDocuments,
    WizardStep.referees => s.stepReferees,
    WizardStep.zones => s.stepZones,
    WizardStep.review => s.stepReview,
  };
}

class _Header extends StatelessWidget {
  const _Header({
    required this.index,
    required this.total,
    required this.title,
  });
  final int index;
  final int total;
  final String title;

  @override
  Widget build(BuildContext context) {
    final s = TeacherStrings.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        HsSpace.lg,
        HsSpace.sm,
        HsSpace.lg,
        HsSpace.xs,
      ),
      child: Semantics(
        container: true,
        label: s.progressSemantic(index + 1, total, title),
        child: ExcludeSemantics(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                s.stepOf(index + 1, total),
                style: Theme.of(context).textTheme.bodySmall,
              ),
              Text(title, style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(height: HsSpace.sm),
              LinearProgressIndicator(value: (index + 1) / total, minHeight: 6),
            ],
          ),
        ),
      ),
    );
  }
}

class _SyncBanner extends StatelessWidget {
  const _SyncBanner({required this.state, required this.onRetry});
  final ApplicationState state;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final s = TeacherStrings.of(context);
    final (text, icon, color) = switch (state.sync) {
      SyncStatus.saved => (
        s.syncSaved,
        Icons.cloud_done_outlined,
        HsColors.success,
      ),
      SyncStatus.saving => (
        s.syncSaving,
        Icons.cloud_upload_outlined,
        HsColors.textMuted,
      ),
      SyncStatus.offline => (
        s.syncOffline,
        Icons.cloud_off_outlined,
        HsColors.warning,
      ),
      SyncStatus.rateLimited => (
        s.syncRateLimited,
        Icons.hourglass_empty,
        HsColors.warning,
      ),
      SyncStatus.conflict => (
        s.syncConflict,
        Icons.sync_problem_outlined,
        HsColors.warning,
      ),
      SyncStatus.locked => (s.syncLocked, Icons.lock_outline, HsColors.warning),
    };
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: HsSpace.lg),
      child: Semantics(
        liveRegion: true,
        child: Row(
          children: [
            Icon(icon, size: 18, color: color),
            const SizedBox(width: HsSpace.sm),
            Expanded(
              child: Text(
                text,
                style: Theme.of(context).textTheme.bodySmall
                    ?.copyWith(color: color),
              ),
            ),
            if (state.sync == SyncStatus.offline ||
                state.sync == SyncStatus.rateLimited)
              TextButton(
                onPressed: onRetry,
                style: TextButton.styleFrom(
                  minimumSize: const Size(
                    HsSize.minTapTarget,
                    HsSize.minTapTarget,
                  ),
                ),
                child: Text(s.tryAgain),
              ),
          ],
        ),
      ),
    );
  }
}

class _ConflictCard extends StatelessWidget {
  const _ConflictCard({required this.onKeepMine, required this.onUseTheirs});
  final VoidCallback onKeepMine;
  final VoidCallback onUseTheirs;

  @override
  Widget build(BuildContext context) {
    final s = TeacherStrings.of(context);
    return Card(
      margin: const EdgeInsets.all(HsSpace.lg),
      color: const Color(0xFFFFF4DC),
      child: Padding(
        padding: const EdgeInsets.all(HsSpace.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              s.conflictTitle,
              style: Theme.of(context).textTheme.titleSmall,
            ),
            const SizedBox(height: HsSpace.xs),
            Text(s.conflictBody),
            const SizedBox(height: HsSpace.sm),
            Wrap(
              spacing: HsSpace.sm,
              children: [
                FilledButton(
                  onPressed: onKeepMine,
                  child: Text(s.conflictKeepMine),
                ),
                OutlinedButton(
                  onPressed: onUseTheirs,
                  child: Text(s.conflictUseTheirs),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _NavBar extends StatelessWidget {
  const _NavBar({
    required this.index,
    required this.last,
    required this.onBack,
    required this.onNext,
  });
  final int index;
  final int last;
  final VoidCallback onBack;
  final VoidCallback onNext;

  @override
  Widget build(BuildContext context) {
    final s = TeacherStrings.of(context);
    return Material(
      elevation: 4,
      child: Padding(
        padding: const EdgeInsets.all(HsSpace.md),
        child: Row(
          children: [
            if (index > 0)
              Expanded(
                child: OutlinedButton(onPressed: onBack, child: Text(s.back)),
              )
            else
              const Spacer(),
            const SizedBox(width: HsSpace.md),
            if (index < last)
              Expanded(
                child: FilledButton(onPressed: onNext, child: Text(s.next)),
              )
            else
              const Spacer(),
          ],
        ),
      ),
    );
  }
}
