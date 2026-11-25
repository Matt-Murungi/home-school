import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

class _Throwing extends FakeAuthService {
  @override
  Future<AuthSession?> refreshSession() async => throw Exception('offline');
}

Widget host(AuthService? auth) => ProviderScope(
  overrides: [if (auth != null) authServiceProvider.overrideWithValue(auth)],
  child: SessionRefresher(child: HsApp(router: buildAppRouter(AppSpec.parent))),
);

void main() {
  testWidgets('returning to the foreground refreshes the session', (t) async {
    final auth = FakeAuthService();
    await t.pumpWidget(host(auth));
    expect(auth.refreshCalls, 0);
    t.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await t.pump();
    final afterFirstResume = auth.refreshCalls;
    expect(afterFirstResume, greaterThan(0));
    for (final s in [
      AppLifecycleState.inactive,
      AppLifecycleState.hidden,
      AppLifecycleState.paused,
    ]) {
      t.binding.handleAppLifecycleStateChanged(s);
    }
    await t.pump();
    expect(auth.refreshCalls, afterFirstResume, reason: 'background: no call');
    for (final s in [
      AppLifecycleState.hidden,
      AppLifecycleState.inactive,
      AppLifecycleState.resumed,
    ]) {
      t.binding.handleAppLifecycleStateChanged(s);
    }
    await t.pump();
    expect(auth.refreshCalls, greaterThan(afterFirstResume));
  });

  testWidgets('a failing refresh (offline) is swallowed', (t) async {
    await t.pumpWidget(host(_Throwing()));
    t.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await t.pump();
    expect(t.takeException(), isNull);
  });

  testWidgets('no auth service wired: resume does nothing and does not crash', (
    t,
  ) async {
    await t.pumpWidget(host(null));
    t.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await t.pump();
    expect(t.takeException(), isNull);
  });
}
