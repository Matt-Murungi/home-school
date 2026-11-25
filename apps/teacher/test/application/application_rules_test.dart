import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_teacher/application/data/fakes.dart';
import 'package:home_school_teacher/application/model/application_draft.dart';
import 'package:home_school_teacher/application/model/rules.dart';
import 'package:home_school_teacher/application/ui/photo.dart';
import 'package:home_school_teacher/application/ui/steps/zones_step.dart';

// The same cases run against the server rules in
// services/core/tests/test_teacher_application_rules.py.
final fixture = jsonDecode(
  File('test/fixtures/application_cases.json').readAsStringSync(),
) as Map<String, Object?>;

Map<String, Object?> merged(Map base, Map patch) {
  final out = Map<String, Object?>.from(base);
  patch.forEach((k, v) {
    out[k as String] = (v is Map && out[k] is Map)
        ? merged(out[k] as Map, v)
        : v;
  });
  return out;
}

void main() {
  final ctx = RuleContext(
    today: DateTime.parse(fixture['today']! as String),
    reference: sampleReference(),
    ownPhone: fixture['own_phone']! as String,
    minWeeklyMinutes: (fixture['min_weekly_hours']! as int) * 60,
  );
  final valid = jsonDecode(
    File('test/fixtures/valid_content.json').readAsStringSync(),
  ) as Map<String, Object?>;

  for (final raw in fixture['cases']! as List) {
    final c = raw as Map<String, Object?>;
    test('shared case ${c['id']}', () {
      var content = c['replace'] == true
          ? Map<String, Object?>.from(c['patch']! as Map)
          : merged(jsonDecode(jsonEncode(valid)) as Map, c['patch']! as Map);
      for (final path in (c['remove'] as List?) ?? const []) {
        final parts = (path as String).split('.');
        Map node = content;
        for (final p in parts.take(parts.length - 1)) {
          node = node[p] as Map;
        }
        node.remove(parts.last);
      }
      final got = validateApplication(
        ApplicationDraft.fromJson(content),
        ctx,
        final_: c['final'] != false,
      ).toSet();
      expect(got, ((c['issues']! as List).cast<String>()).toSet());
    });
  }

  test('the sample complete draft has no issues', () {
    expect(validateApplication(completeDraft(), ctx, final_: true), isEmpty);
  });

  test('peak windows: weekdays 15-20, weekends 08-18 (NFR-5)', () {
    expect(isPeak(1, 15 * 60, 16 * 60), isTrue);
    expect(isPeak(1, 9 * 60, 15 * 60), isFalse);
    expect(isPeak(6, 8 * 60, 9 * 60), isTrue);
    expect(isPeak(7, 18 * 60, 19 * 60), isFalse);
    expect(isPeak(2, 19 * 60, 21 * 60), isTrue);
  });

  test('hour blocks merge into contiguous slots and back', () {
    final slots = slotsFromBlocks({(1, 15), (1, 16), (1, 18), (3, 6)});
    expect(slots, [
      const AvailabilitySlot(1, 900, 1020),
      const AvailabilitySlot(1, 1080, 1140),
      const AvailabilitySlot(3, 360, 420),
    ]);
    expect(blocksFromSlots(slots), {(1, 15), (1, 16), (1, 18), (3, 6)});
    expect(
      validateApplication(
        ApplicationDraft(availability: slots),
        ctx,
        final_: false,
      ),
      isEmpty,
    );
  });

  test('text cleaning matches the server', () {
    expect(cleanText('  Ana  Maria\t\n '), 'Ana Maria');
    expect(stepOfIssue('referees[1].phone:invalid'), WizardStep.referees);
    expect(
      stepOfIssue('curricula[0].level_ids:required'),
      WizardStep.qualifications,
    );
    expect(stepOfIssue('availability:too_few_hours'), WizardStep.zones);
    expect(stepOfIssue('personal.photo:required'), WizardStep.personal);
  });

  test('the draft round-trips through JSON without the local photo path for the server', () {
    final d = completeDraft();
    expect(d.personal.photo!.toJson()['local_path'], '/data/photo.jpg');
    final server = d.toJson(forServer: true);
    expect(jsonEncode(server).contains('local_path'), isFalse);
    expect(jsonEncode(server).contains('/data/'), isFalse);
    expect(
      ApplicationDraft.fromJson(jsonDecode(jsonEncode(d.toJson()))).toJson(),
      d.toJson(),
    );
  });

  test(
    'the app never sends a photo file id, even if an old server copy had one',
    () {
      final d = ApplicationDraft.fromJson({
        'personal': {
          'photo': {
            'state': 'chosen',
            'file_id': 'someone_elses_file',
            'local_path': '/p.jpg',
          },
        },
      });
      expect(
        d.personal.photo!.fileId,
        'someone_elses_file',
      ); // read back so the rules flag it
      expect(validateApplication(d, ctx, final_: false), [
        'personal.photo:file_not_allowed',
      ]);
      final sent = d.toJson(forServer: true).toString();
      expect(sent.contains('file_id'), isFalse);
      expect(sent.contains('someone_elses_file'), isFalse);
      expect(sent.contains('/p.jpg'), isFalse);
    },
  );

  group('computeCrop (numeric)', () {
    test('zoom 1 is the largest square: the whole shorter edge', () {
      final c = computeCrop(zoom: 1, panX: .5, panY: .5, aspect: 3 / 4);
      expect(c.size, 1);
      expect(c.x, 0); // portrait: no free space sideways
      expect(
        c.y,
        closeTo((1 - 3 / 4) * .5, 1e-9),
      ); // 3:4 leaves 25% of the height free
    });

    test(
      'portrait 3:4 at zoom 2: half the width, pan moves inside the free space',
      () {
        final top = computeCrop(zoom: 2, panX: 0, panY: 0, aspect: 3 / 4);
        final bottom = computeCrop(zoom: 2, panX: 1, panY: 1, aspect: 3 / 4);
        expect(top.size, .5);
        expect((top.x, top.y), (0.0, 0.0));
        expect(bottom.x, closeTo(.5, 1e-9)); // 1 - 0.5 of the width
        expect(
          bottom.y,
          closeTo(1 - .5 * 3 / 4, 1e-9),
        ); // side is .5 of width = .375 of height
      },
    );

    test('landscape 4:3 at zoom 1: full height, horizontal free space', () {
      final c = computeCrop(zoom: 1, panX: 1, panY: 0, aspect: 4 / 3);
      expect(c.size, 1);
      expect(c.x, closeTo(1 - 3 / 4, 1e-9));
      expect(c.y, 0);
    });

    test('zoom and pan are clamped: never outside the photo', () {
      for (final z in [0.2, 1, 1.7, 4, 9]) {
        for (final p in [-1.0, 0.0, .3, 1.0, 2.0]) {
          for (final a in [3 / 4, 1.0, 4 / 3]) {
            final c = computeCrop(
              zoom: z.toDouble(),
              panX: p,
              panY: p,
              aspect: a,
            );
            final w = a >= 1 ? c.size / a : c.size;
            final h = a >= 1 ? c.size : c.size * a;
            expect(c.size, inInclusiveRange(.25, 1));
            expect(c.x, inInclusiveRange(0, 1 - w + 1e-9));
            expect(c.y, inInclusiveRange(0, 1 - h + 1e-9));
          }
        }
      }
    });

    test('the crop it produces always passes the shared rules', () {
      final c = computeCrop(zoom: 3, panX: 1, panY: 1, aspect: 3 / 4);
      final d = ApplicationDraft(
        personal: Personal(
          photo: PhotoChoice(state: 'chosen', crop: c),
        ),
      );
      expect(validateApplication(d, ctx, final_: false), isEmpty);
    });
  });
}
