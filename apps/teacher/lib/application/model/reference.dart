class NamedId {
  const NamedId(this.id, this.name);
  final String id;
  final String name;
}

class CurriculumOption {
  const CurriculumOption({
    required this.id,
    required this.code,
    required this.name,
    required this.levels,
    required this.subjects,
  });
  final String id;
  final String code;
  final String name;
  final List<NamedId> levels;
  final List<NamedId> subjects;
}

class ZoneOption {
  const ZoneOption(this.id, this.name, {this.hardToFill = false});
  final String id;
  final String name;
  final bool hardToFill;
}

/// What the wizard can choose from: the published catalogue (via the catalogue
/// read interface, see [ReferenceSource]) and the zones.
class ReferenceData {
  const ReferenceData({required this.curricula, required this.zones});
  final List<CurriculumOption> curricula;
  final List<ZoneOption> zones;

  CurriculumOption? curriculum(String id) {
    for (final c in curricula) {
      if (c.id == id) return c;
    }
    return null;
  }

  Map<String, Object?> toJson() => {
    'curricula': [
      for (final c in curricula)
        {
          'id': c.id,
          'code': c.code,
          'name': c.name,
          'levels': [
            for (final l in c.levels) {'id': l.id, 'name': l.name},
          ],
          'subjects': [
            for (final x in c.subjects) {'id': x.id, 'name': x.name},
          ],
        },
    ],
    'zones': [
      for (final z in zones)
        {'id': z.id, 'name': z.name, 'hard_to_fill': z.hardToFill},
    ],
  };

  static ReferenceData fromJson(Map<String, Object?> j) {
    List<NamedId> named(Object? l) => [
      for (final x in (l as List? ?? const []))
        NamedId((x as Map)['id'] as String, x['name'] as String),
    ];
    return ReferenceData(
      curricula: [
        for (final c in (j['curricula'] as List? ?? const []))
          CurriculumOption(
            id: (c as Map)['id'] as String,
            code: c['code'] as String,
            name: c['name'] as String,
            levels: named(c['levels']),
            subjects: named(c['subjects']),
          ),
      ],
      zones: [
        for (final z in (j['zones'] as List? ?? const []))
          ZoneOption(
            (z as Map)['id'] as String,
            z['name'] as String,
            hardToFill: z['hard_to_fill'] == true,
          ),
      ],
    );
  }
}
