import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_school_shared/home_school_shared.dart';

import '../../../l10n/gen/teacher_strings.dart';
import '../../model/application_draft.dart';
import '../../model/rules.dart';
import '../../state/application_controller.dart';
import '../widgets.dart';

/// Hour blocks offered in the grid: 06:00 .. 20:00 starts (each one hour).
final gridHours = [for (var h = 6; h < 21; h++) h];

/// Merge selected (weekday, hour) blocks into contiguous slots.
List<AvailabilitySlot> slotsFromBlocks(Set<(int, int)> blocks) {
  final out = <AvailabilitySlot>[];
  for (var day = 1; day <= 7; day++) {
    final hours = [
      for (final (d, h) in blocks)
        if (d == day) h,
    ]..sort();
    int? start;
    int? prev;
    for (final h in hours) {
      if (start != null && prev != null && h == prev + 1) {
        prev = h;
        continue;
      }
      if (start != null) {
        out.add(AvailabilitySlot(day, start * 60, (prev! + 1) * 60));
      }
      start = h;
      prev = h;
    }
    if (start != null) {
      out.add(AvailabilitySlot(day, start * 60, (prev! + 1) * 60));
    }
  }
  return out;
}

/// Blocks fully covered by [slots].
Set<(int, int)> blocksFromSlots(List<AvailabilitySlot> slots) => {
  for (final sl in slots)
    for (final h in gridHours)
      if (sl.startMinute <= h * 60 && sl.endMinute >= (h + 1) * 60)
        (sl.weekday, h),
};

/// T-07 Zones and availability.
class ZonesStep extends ConsumerWidget {
  const ZonesStep({super.key, required this.issues});
  final List<String> issues;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final s = TeacherStrings.of(context);
    final st = ref.watch(applicationControllerProvider);
    final c = ref.read(applicationControllerProvider.notifier);
    final zones = st.reference?.zones;
    final radius = st.draft.travelRadiusKm;
    final blocks = blocksFromSlots(st.draft.availability);
    final total = weeklyMinutes(st.draft.availability);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SectionTitle(s.zonesTitle),
        Text(s.zonesHelp),
        const SizedBox(height: HsSpace.sm),
        if (zones == null)
          st.referenceFailed
              ? Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(s.catalogueUnavailable),
                    OutlinedButton(
                      onPressed: c.retryReference,
                      child: Text(s.tryAgain),
                    ),
                  ],
                )
              : Semantics(
                  label: s.loadingZones,
                  child: const Center(child: CircularProgressIndicator()),
                )
        else
          Wrap(
            spacing: HsSpace.sm,
            runSpacing: HsSpace.sm,
            children: [
              for (final z in zones)
                FilterChip(
                  label: Text(z.name),
                  selected: st.draft.zoneIds.contains(z.id),
                  materialTapTargetSize: MaterialTapTargetSize.padded,
                  onSelected: (on) => c.update((d) {
                    final ids = [...d.zoneIds];
                    on ? ids.add(z.id) : ids.remove(z.id);
                    return d.copyWith(zoneIds: ids);
                  }),
                ),
            ],
          ),
        if (issueText(s, issues, 'zone_ids') != null)
          ErrorLine(issueText(s, issues, 'zone_ids')!),
        const SizedBox(height: HsSpace.lg),
        SectionTitle(s.radiusTitle),
        Row(
          children: [
            IconButton(
              tooltip: s.radiusLess,
              constraints: const BoxConstraints(
                minWidth: HsSize.minTapTarget,
                minHeight: HsSize.minTapTarget,
              ),
              onPressed: () => c.update(
                (d) => d.copyWith(
                  travelRadiusKm: ((d.travelRadiusKm ?? 5) - 1).clamp(
                    radiusRange.$1,
                    radiusRange.$2,
                  ),
                ),
              ),
              icon: const Icon(Icons.remove_circle_outline),
            ),
            Expanded(
              child: Semantics(
                liveRegion: true,
                child: Text(
                  radius == null ? s.radiusNotSet : s.radiusValue(radius),
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ),
            ),
            IconButton(
              tooltip: s.radiusMore,
              constraints: const BoxConstraints(
                minWidth: HsSize.minTapTarget,
                minHeight: HsSize.minTapTarget,
              ),
              onPressed: () => c.update(
                (d) => d.copyWith(
                  travelRadiusKm: ((d.travelRadiusKm ?? 5) + 1).clamp(
                    radiusRange.$1,
                    radiusRange.$2,
                  ),
                ),
              ),
              icon: const Icon(Icons.add_circle_outline),
            ),
          ],
        ),
        if (issueText(s, issues, 'travel_radius_km') != null)
          ErrorLine(issueText(s, issues, 'travel_radius_km')!),
        const Divider(height: HsSpace.xxl),
        SectionTitle(s.availabilityTitle),
        Text(s.availabilityHelp),
        const SizedBox(height: HsSpace.xs),
        Semantics(
          container: true,
          child: Row(
            children: [
              const _PeakSwatch(),
              const SizedBox(width: HsSpace.sm),
              Expanded(child: Text(s.peakLegend)),
            ],
          ),
        ),
        const SizedBox(height: HsSpace.md),
        for (var day = 1; day <= 7; day++)
          _DayRow(
            weekday: day,
            selected: {
              for (final (d, h) in blocks)
                if (d == day) h,
            },
            onToggle: (h) {
              final next = {...blocks};
              if (!next.remove((day, h))) next.add((day, h));
              c.update((d) => d.copyWith(availability: slotsFromBlocks(next)));
            },
          ),
        const SizedBox(height: HsSpace.md),
        Semantics(
          liveRegion: true,
          child: Text(
            s.availabilityTotal(total ~/ 60),
            style: Theme.of(context).textTheme.titleSmall,
          ),
        ),
        Text(s.availabilityMinimum(defaultMinWeeklyHours)),
        for (final i in issues.where((i) => i.startsWith('availability')))
          ErrorLine(issueText(s, [i], i.split(':').first)!),
      ],
    );
  }
}

class _PeakSwatch extends StatelessWidget {
  const _PeakSwatch();
  @override
  Widget build(BuildContext context) => Container(
    width: 20,
    height: 20,
    decoration: BoxDecoration(
      color: HsColors.accent.withValues(alpha: .25),
      border: Border.all(color: HsColors.accent, width: 2),
      borderRadius: BorderRadius.circular(4),
    ),
  );
}

class _DayRow extends StatelessWidget {
  const _DayRow({
    required this.weekday,
    required this.selected,
    required this.onToggle,
  });
  final int weekday;
  final Set<int> selected;
  final ValueChanged<int> onToggle;

  @override
  Widget build(BuildContext context) {
    final s = TeacherStrings.of(context);
    final names = [
      s.dayMon,
      s.dayTue,
      s.dayWed,
      s.dayThu,
      s.dayFri,
      s.daySat,
      s.daySun,
    ];
    final (lo, hi) = peakWindow(weekday);
    return Padding(
      padding: const EdgeInsets.only(bottom: HsSpace.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Semantics(
            header: true,
            child: Text(
              names[weekday - 1],
              style: Theme.of(context).textTheme.titleSmall,
            ),
          ),
          const SizedBox(height: HsSpace.xs),
          Wrap(
            spacing: HsSpace.xs,
            runSpacing: HsSpace.xs,
            children: [
              for (final h in gridHours)
                _Block(
                  hour: h,
                  peak: h * 60 < hi && (h + 1) * 60 > lo,
                  selected: selected.contains(h),
                  label: '${names[weekday - 1]} ${_hh(h)}-${_hh(h + 1)}',
                  onTap: () => onToggle(h),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

String _hh(int h) => '${h.toString().padLeft(2, '0')}:00';

class _Block extends StatelessWidget {
  const _Block({
    required this.hour,
    required this.peak,
    required this.selected,
    required this.label,
    required this.onTap,
  });
  final int hour;
  final bool peak;
  final bool selected;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final s = TeacherStrings.of(context);
    return Semantics(
      button: true,
      selected: selected,
      label: peak ? '$label, ${s.peakSemantic}' : label,
      excludeSemantics: true,
      onTap: onTap,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(8),
        child: Container(
          constraints: const BoxConstraints(
            minWidth: HsSize.minTapTarget,
            minHeight: HsSize.minTapTarget,
          ),
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(horizontal: HsSpace.sm),
          decoration: BoxDecoration(
            color: selected
                ? HsColors.primary
                : (peak
                      ? HsColors.accent.withValues(alpha: .25)
                      : HsColors.surface),
            border: Border.all(
              color: selected
                  ? HsColors.primary
                  : (peak ? HsColors.accent : HsColors.border),
              width: peak ? 2 : 1,
            ),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Text(
            hour.toString().padLeft(2, '0'),
            style: TextStyle(
              color: selected ? Colors.white : HsColors.text,
              fontWeight: peak ? FontWeight.w700 : FontWeight.w500,
            ),
          ),
        ),
      ),
    );
  }
}
