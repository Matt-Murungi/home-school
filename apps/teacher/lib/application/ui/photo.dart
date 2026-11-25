import 'dart:io' show File;
import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:home_school_shared/home_school_shared.dart';
import 'package:image_picker/image_picker.dart';

import '../../l10n/gen/teacher_strings.dart';
import '../model/application_draft.dart';
import 'widgets.dart' show ErrorLine;

class PickedPhoto {
  const PickedPhoto(this.path, this.width, this.height);
  final String path;
  final int width;
  final int height;
}

enum PhotoSource { camera, gallery }

/// Camera or gallery. The real adapter wraps `image_picker`; tests use
/// [FakePhotoPicker]. The photo is not uploaded here (V02 uploads it).
abstract interface class PhotoPicker {
  Future<PickedPhoto?> pick(PhotoSource source);
}

class FakePhotoPicker implements PhotoPicker {
  FakePhotoPicker({
    this.result = const PickedPhoto(
      '/data/user/0/ug.homeschool/cache/photo.jpg',
      1200,
      1600,
    ),
    this.throwOnPick = false,
  });
  PickedPhoto? result;
  bool throwOnPick;
  final requested = <PhotoSource>[];

  @override
  Future<PickedPhoto?> pick(PhotoSource source) async {
    requested.add(source);
    if (throwOnPick) throw StateError('permission denied');
    return result;
  }
}

class ImagePickerPhotoPicker implements PhotoPicker {
  ImagePickerPhotoPicker([ImagePicker? picker])
    : _picker = picker ?? ImagePicker();
  final ImagePicker _picker;

  @override
  Future<PickedPhoto?> pick(PhotoSource source) async {
    // maxWidth keeps memory low on 2 GB phones; the crop is stored as fractions.
    final f = await _picker.pickImage(
      source: source == PhotoSource.camera
          ? ImageSource.camera
          : ImageSource.gallery,
      maxWidth: 1600,
      maxHeight: 1600,
      imageQuality: 85,
      preferredCameraDevice: CameraDevice.front,
    );
    if (f == null) return null;
    return PickedPhoto(
      f.path,
      0,
      0,
    ); // size unknown: crop UI assumes portrait 3:4
  }
}

final photoPickerProvider = Provider<PhotoPicker>(
  (ref) => ImagePickerPhotoPicker(),
);

/// Crop maths. [zoom] >= 1 (1 = the largest square), [panX], [panY] in 0..1
/// move the square inside the free space. Result fractions: x, y of the
/// top-left corner (of width / height) and the side (of the shorter edge).
CropRect computeCrop({
  required double zoom,
  required double panX,
  required double panY,
  required double aspect, // width / height of the photo
}) {
  final z = zoom.clamp(1.0, 4.0);
  final side = 1 / z; // fraction of the shorter edge
  final wFrac = aspect >= 1 ? side / aspect : side; // as fraction of width
  final hFrac = aspect >= 1 ? side : side * aspect; // as fraction of height
  final x = (1 - wFrac) * panX.clamp(0.0, 1.0);
  final y = (1 - hFrac) * panY.clamp(0.0, 1.0);
  return CropRect(x, y, side);
}

/// Photo section of T-03: guidance, choose, crop, replace.
class PhotoSection extends ConsumerWidget {
  const PhotoSection({
    super.key,
    required this.photo,
    required this.onChanged,
    this.error,
  });
  final PhotoChoice? photo;
  final ValueChanged<PhotoChoice?> onChanged;
  final String? error;

  Future<void> _choose(
    BuildContext context,
    WidgetRef ref,
    PhotoSource src,
  ) async {
    final s = TeacherStrings.of(context);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final p = await ref.read(photoPickerProvider).pick(src);
      if (p == null) return;
      final choice = PhotoChoice(
        state: 'chosen',
        localPath: p.path,
        crop: const CropRect(0, 0, 1),
      );
      onChanged(choice);
      if (context.mounted) await _crop(context, choice, p.width, p.height);
    } on Object {
      messenger.showSnackBar(SnackBar(content: Text(s.photoPickFailed)));
    }
  }

  Future<void> _crop(
    BuildContext context,
    PhotoChoice choice,
    int w,
    int h,
  ) async {
    final aspect = (w > 0 && h > 0) ? w / h : 3 / 4;
    final crop = await Navigator.of(context).push<CropRect>(
      MaterialPageRoute(
        builder: (_) =>
            PhotoCropScreen(path: choice.localPath ?? '', aspect: aspect),
      ),
    );
    if (crop != null) onChanged(choice.withCrop(crop));
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final s = TeacherStrings.of(context);
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(s.photoTitle, style: theme.textTheme.titleMedium),
        const SizedBox(height: HsSpace.sm),
        Card(
          color: HsColors.primarySoft,
          child: Padding(
            padding: const EdgeInsets.all(HsSpace.md),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(s.photoGuidanceTitle, style: theme.textTheme.titleSmall),
                const SizedBox(height: HsSpace.xs),
                for (final line in [
                  s.photoGuide1,
                  s.photoGuide2,
                  s.photoGuide3,
                  s.photoGuide4,
                ])
                  Padding(
                    padding: const EdgeInsets.only(top: HsSpace.xs),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Icon(
                          Icons.check_circle_outline,
                          size: 18,
                          color: HsColors.primary,
                        ),
                        const SizedBox(width: HsSpace.sm),
                        Expanded(child: Text(line)),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ),
        const SizedBox(height: HsSpace.sm),
        if (photo != null) _Preview(photo: photo!),
        Wrap(
          spacing: HsSpace.sm,
          runSpacing: HsSpace.sm,
          children: [
            OutlinedButton.icon(
              onPressed: () => _choose(context, ref, PhotoSource.camera),
              icon: const Icon(Icons.photo_camera_outlined),
              label: Text(s.photoTake),
            ),
            OutlinedButton.icon(
              onPressed: () => _choose(context, ref, PhotoSource.gallery),
              icon: const Icon(Icons.photo_library_outlined),
              label: Text(s.photoChoose),
            ),
            if (photo?.localPath != null)
              TextButton(
                onPressed: () => _crop(context, photo!, 0, 0),
                child: Text(s.photoAdjust),
              ),
            if (photo != null)
              TextButton(
                onPressed: () => onChanged(null),
                child: Text(s.photoRemove),
              ),
          ],
        ),
        if (error != null) ErrorLine(error!),
        const SizedBox(height: HsSpace.lg),
      ],
    );
  }
}

class _Preview extends StatelessWidget {
  const _Preview({required this.photo});
  final PhotoChoice photo;

  @override
  Widget build(BuildContext context) {
    final s = TeacherStrings.of(context);
    final path = photo.localPath;
    return Semantics(
      label: s.photoPreviewLabel,
      image: true,
      child: Container(
        width: 96,
        height: 96,
        margin: const EdgeInsets.only(bottom: HsSpace.sm),
        clipBehavior: Clip.antiAlias,
        decoration: BoxDecoration(
          color: HsColors.border,
          borderRadius: BorderRadius.circular(HsRadius.card),
        ),
        child: (path == null || kIsWeb)
            ? const Icon(Icons.person_outline, size: 48)
            : Image.file(
                // Decoded at thumbnail size: no full-size bitmap on low-end phones.
                File(path),
                cacheWidth: 192,
                fit: BoxFit.cover,
                errorBuilder: (_, _, _) =>
                    const Icon(Icons.person_outline, size: 48),
              ),
      ),
    );
  }
}

/// Pick the part of the photo to keep: zoom and move a square.
class PhotoCropScreen extends StatefulWidget {
  const PhotoCropScreen({super.key, required this.path, required this.aspect});
  final String path;
  final double aspect;

  @override
  State<PhotoCropScreen> createState() => _PhotoCropScreenState();
}

class _PhotoCropScreenState extends State<PhotoCropScreen> {
  double zoom = 1, panX = .5, panY = .3;

  @override
  Widget build(BuildContext context) {
    final s = TeacherStrings.of(context);
    final crop = computeCrop(
      zoom: zoom,
      panX: panX,
      panY: panY,
      aspect: widget.aspect,
    );
    return Scaffold(
      appBar: AppBar(title: Text(s.cropTitle)),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(HsSpace.lg),
          children: [
            Text(s.cropHelp),
            const SizedBox(height: HsSpace.lg),
            _slider(s.cropZoom, zoom, 1, 4, (v) => setState(() => zoom = v)),
            _slider(
              s.cropLeftRight,
              panX,
              0,
              1,
              (v) => setState(() => panX = v),
            ),
            _slider(s.cropUpDown, panY, 0, 1, (v) => setState(() => panY = v)),
            const SizedBox(height: HsSpace.lg),
            FilledButton(
              onPressed: () => Navigator.of(context).pop(
                CropRect(
                  double.parse(crop.x.toStringAsFixed(4)),
                  double.parse(crop.y.toStringAsFixed(4)),
                  double.parse(math.min(crop.size, 1).toStringAsFixed(4)),
                ),
              ),
              child: Text(s.cropDone),
            ),
          ],
        ),
      ),
    );
  }

  Widget _slider(
    String label,
    double v,
    double lo,
    double hi,
    ValueChanged<double> on,
  ) => Semantics(
    label: label,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label),
        Slider(
          value: v,
          min: lo,
          max: hi,
          semanticFormatterCallback: (x) => x.toStringAsFixed(1),
          onChanged: on,
        ),
      ],
    ),
  );
}
