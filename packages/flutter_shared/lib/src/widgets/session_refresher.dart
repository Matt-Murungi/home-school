import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/session_controller.dart';

/// Extends the stored session when the app returns to the foreground (A01:
/// "session persisted securely and refreshed"). Failures are ignored here:
/// being offline must never sign anyone out.
class SessionRefresher extends ConsumerStatefulWidget {
  const SessionRefresher({super.key, required this.child});
  final Widget child;

  @override
  ConsumerState<SessionRefresher> createState() => _SessionRefresherState();
}

class _SessionRefresherState extends ConsumerState<SessionRefresher>
    with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) return;
    _refresh();
  }

  Future<void> _refresh() async {
    try {
      // Re-reads labels and status too, so a role change re-routes (A03).
      await ref.read(sessionProvider.notifier).refresh();
    } on Object {
      // No auth service wired (tests) or a transient failure: keep going.
    }
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
