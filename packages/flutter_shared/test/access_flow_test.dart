import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:home_school_shared/home_school_shared.dart';

const _phoneField = Key('otp-phone-field');
const _codeField = Key('otp-code-field');

/// Counts every build of a protected tab: "no flash of protected content"
/// means this stays 0 whenever the user may not see it.
class Probe {
  int builds = 0;
  Widget build(BuildContext c, TabSpec tab) {
    builds++;
    return Text('PROTECTED ${tab.path}');
  }
}

class SlowAuth extends FakeAuthService {
  final gate = Completer<void>();
  @override
  Future<AuthSession?> refreshSession() async {
    final s = await super.refreshSession(); // read now, answer when released
    await gate.future;
    return s;
  }
}

class FlakyAuth extends FakeAuthService {
  bool failRefresh = false;
  @override
  Future<AuthSession?> refreshSession() async {
    if (failRefresh) throw Exception('offline');
    return super.refreshSession();
  }
}

class FakeAccess implements AccessSource {
  AccountStatus? answer;
  Completer<void>? hold;
  @override
  Future<AccountStatus?> status(AppId app) async {
    if (hold != null) await hold!.future;
    return answer;
  }
}

late GoRouter lastRouter;

Widget host(
  AuthService auth, {
  AppSpec? spec,
  String initial = '/',
  Probe? probe,
  AccessSource? access,
  double textScale = 1,
  String Function(AuthSession)? landing,
}) {
  lastRouter = buildAppRouter(
    spec ?? AppSpec.parent,
    initialLocation: initial,
    tabBuilder: probe?.build,
    landingPath: landing,
  );
  return ProviderScope(
    overrides: [
      authServiceProvider.overrideWithValue(auth),
      if (access != null) accessSourceProvider.overrideWithValue(access),
    ],
    child: MediaQuery(
      data: MediaQueryData(textScaler: TextScaler.linear(textScale)),
      child: HsApp(router: lastRouter),
    ),
  );
}

Future<void> signInThroughThePhoneFlow(WidgetTester t) async {
  await t.enterText(find.byKey(_phoneField), '0772123456');
  await t.pump();
  await t.tap(find.byKey(const Key('otp-send')));
  await t.pumpAndSettle();
  await t.enterText(find.byKey(_codeField), '123456');
  await t.pumpAndSettle();
}

const tabsOf = {
  'parent': ['home', 'sessions', 'progress', 'billing', 'account'],
  'teacher': ['today', 'schedule', 'earnings', 'growth', 'account'],
};

void main() {
  group('signed out: every protected route goes to the auth flow', () {
    for (final spec in [AppSpec.parent, AppSpec.teacher]) {
      for (final tab in tabsOf[spec.id.wire]!) {
        testWidgets('${spec.id.wire} app, deep link /$tab', (t) async {
          final probe = Probe();
          await t.pumpWidget(
            host(FakeAuthService(), spec: spec, initial: '/$tab', probe: probe),
          );
          await t.pumpAndSettle();
          expect(find.text('Enter your phone number'), findsOneWidget);
          expect(probe.builds, 0);
          expect(find.textContaining('PROTECTED'), findsNothing);
        });
      }
    }

    testWidgets('unknown routes are guarded too (deny by default)', (t) async {
      final probe = Probe();
      await t.pumpWidget(
        host(FakeAuthService(), initial: '/brand-new', probe: probe),
      );
      await t.pumpAndSettle();
      expect(find.text('Enter your phone number'), findsOneWidget);
    });

    testWidgets('the deep link is remembered and opened after sign-in', (
      t,
    ) async {
      final probe = Probe();
      final auth = FakeAuthService();
      await t.pumpWidget(host(auth, initial: '/billing', probe: probe));
      await t.pumpAndSettle();
      expect(probe.builds, 0);
      await signInThroughThePhoneFlow(t);
      expect(find.text('PROTECTED billing'), findsOneWidget);
    });

    testWidgets('a forged from target lands on home, not off-app', (t) async {
      final probe = Probe();
      await t.pumpWidget(
        host(
          FakeAuthService(),
          initial: '/sign-in/phone?from=https%3A%2F%2Fevil.example',
          probe: probe,
        ),
      );
      await t.pumpAndSettle();
      await signInThroughThePhoneFlow(t);
      expect(find.text('PROTECTED home'), findsOneWidget);
    });

    testWidgets('the welcome screen is still reachable', (t) async {
      await t.pumpWidget(host(FakeAuthService()));
      await t.pumpAndSettle();
      expect(find.text('Welcome to Home School'), findsOneWidget);
    });
  });

  group('role x app (restored session)', () {
    const roles = [
      'parent',
      'guardian',
      'teacher',
      'opsadmin',
      'curator',
      'safeguardinglead',
      'superadmin',
    ];
    for (final spec in [AppSpec.parent, AppSpec.teacher]) {
      for (final role in roles) {
        testWidgets('${spec.id.wire} app with a $role account', (t) async {
          final probe = Probe();
          final auth = FakeAuthService()..signInAs([role]);
          final first = spec.tabs.first.path;
          await t.pumpWidget(
            host(auth, spec: spec, initial: '/$first', probe: probe),
          );
          await t.pumpAndSettle();
          if (spec.id.admits.contains(role)) {
            expect(find.text('PROTECTED $first'), findsOneWidget);
          } else {
            expect(probe.builds, 0, reason: 'protected content must not build');
            expect(find.byType(WrongAppScreen), findsOneWidget);
            expect(find.text('Sign out'), findsOneWidget);
          }
        });
      }
    }

    for (final spec in [AppSpec.parent, AppSpec.teacher]) {
      testWidgets(
        'a mixed parent and teacher account sees the ${spec.id.wire} app',
        (t) async {
          final probe = Probe();
          final auth = FakeAuthService()..signInAs(['parent', 'teacher']);
          await t.pumpWidget(
            host(
              auth,
              spec: spec,
              initial: '/${spec.tabs.first.path}',
              probe: probe,
            ),
          );
          await t.pumpAndSettle();
          expect(find.byType(WrongAppScreen), findsNothing);
          expect(
            find.text('PROTECTED ${spec.tabs.first.path}'),
            findsOneWidget,
          );
        },
      );
    }
  });

  group('wrong app screen', () {
    testWidgets('a teacher in the parent app: helpful message and sign out', (
      t,
    ) async {
      final auth = FakeAuthService()..signInAs(['teacher']);
      await t.pumpWidget(host(auth, initial: '/home'));
      await t.pumpAndSettle();
      expect(find.text('This is the parent app'), findsOneWidget);
      expect(
        find.text(
          'Your account is a teacher account. Please open the Home School '
          'teacher app instead.',
        ),
        findsOneWidget,
      );
      await t.tap(find.text('Sign out'));
      await t.pumpAndSettle();
      expect(find.text('Welcome to Home School'), findsOneWidget);
      expect(await auth.currentSession(), isNull);
    });

    testWidgets('a parent in the teacher app says "This is the teacher app"', (
      t,
    ) async {
      final auth = FakeAuthService()..signInAs(['parent']);
      await t.pumpWidget(host(auth, spec: AppSpec.teacher, initial: '/today'));
      await t.pumpAndSettle();
      expect(find.text('This is the teacher app'), findsOneWidget);
      expect(find.textContaining('parent app'), findsOneWidget);
    });

    testWidgets('a staff account is pointed to the admin dashboard', (t) async {
      final auth = FakeAuthService()..signInAs(['opsadmin']);
      await t.pumpWidget(host(auth, initial: '/home'));
      await t.pumpAndSettle();
      expect(find.textContaining('admin dashboard'), findsOneWidget);
    });

    testWidgets('an account with no role gets a plain no-access message', (
      t,
    ) async {
      final auth = FakeAuthService()..signInAs(['ops_admin', 'OpsAdmin']);
      await t.pumpWidget(host(auth, initial: '/home'));
      await t.pumpAndSettle();
      expect(find.text('No access with this account'), findsOneWidget);
    });

    testWidgets('signing in with the wrong role ends on the wrong-app screen', (
      t,
    ) async {
      final probe = Probe();
      final auth = FakeAuthService()..signUpLabels = ['teacher'];
      await t.pumpWidget(host(auth, probe: probe));
      await t.pumpAndSettle();
      await t.tap(find.text('Get started'));
      await t.pumpAndSettle();
      await signInThroughThePhoneFlow(t);
      expect(find.text('This is the parent app'), findsOneWidget);
      expect(probe.builds, 0);
    });

    testWidgets('back does not leave the screen for protected content', (
      t,
    ) async {
      final probe = Probe();
      final auth = FakeAuthService()..signInAs(['teacher']);
      await t.pumpWidget(host(auth, initial: '/billing', probe: probe));
      await t.pumpAndSettle();
      expect(lastRouter.canPop(), isFalse);
      lastRouter.go('/billing');
      await t.pumpAndSettle();
      expect(find.byType(WrongAppScreen), findsOneWidget);
      expect(probe.builds, 0);
    });
  });

  group('suspended', () {
    testWidgets('a restored suspended teacher sees the suspended state', (
      t,
    ) async {
      final probe = Probe();
      final auth = FakeAuthService()
        ..signInAs(['teacher'], accountStatus: AccountStatus.suspended);
      await t.pumpWidget(
        host(auth, spec: AppSpec.teacher, initial: '/today', probe: probe),
      );
      await t.pumpAndSettle();
      expect(find.text('Your account is suspended'), findsOneWidget);
      expect(find.byType(WrongAppScreen), findsNothing);
      expect(probe.builds, 0);
      await t.tap(find.text('Sign out'));
      await t.pumpAndSettle();
      expect(find.text('Welcome to Home School'), findsOneWidget);
    });

    testWidgets('a suspended code sign-in shows the state, not an error', (
      t,
    ) async {
      final auth = FakeAuthService()
        ..verifyFailures.add(const OtpException(OtpFailure.suspended));
      await t.pumpWidget(host(auth, spec: AppSpec.teacher));
      await t.pumpAndSettle();
      await t.tap(find.text('Get started'));
      await t.pumpAndSettle();
      await signInThroughThePhoneFlow(t);
      expect(find.text('Your account is suspended'), findsOneWidget);
      expect(find.byKey(const Key('otp-error')), findsNothing);
      await t.tap(find.text('Back'));
      await t.pumpAndSettle();
      expect(find.text('Welcome to Home School'), findsOneWidget);
    });

    testWidgets('suspension reported by the core service while signed in', (
      t,
    ) async {
      final auth = FakeAuthService()..signInAs(['teacher']);
      final access = FakeAccess()..answer = AccountStatus.suspended;
      await t.pumpWidget(
        host(auth, spec: AppSpec.teacher, initial: '/today', access: access),
      );
      await t.pumpAndSettle();
      expect(find.text('Your account is suspended'), findsOneWidget);
      access.answer = AccountStatus.active; // reinstated
      t.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await t.pumpAndSettle();
      expect(find.text('Today'), findsWidgets);
      expect(find.text('Your account is suspended'), findsNothing);
    });

    testWidgets('the suspended screen is not reachable by a healthy user', (
      t,
    ) async {
      final probe = Probe();
      final auth = FakeAuthService()..signInAs(['parent']);
      await t.pumpWidget(host(auth, initial: '/suspended', probe: probe));
      await t.pumpAndSettle();
      expect(find.text('PROTECTED home'), findsOneWidget);
    });
  });

  group('staff and revoked accounts on the phone flow', () {
    for (final entry in {
      OtpFailure.staffAccount:
          'Staff accounts sign in to the admin dashboard on the web, not in '
          'this app.',
      OtpFailure.roleRevoked:
          "This account can't use this app. Please contact support.",
    }.entries) {
      testWidgets('${entry.key.name} is explained on the code screen', (
        t,
      ) async {
        final auth = FakeAuthService()
          ..verifyFailures.add(OtpException(entry.key));
        await t.pumpWidget(host(auth));
        await t.pumpAndSettle();
        await t.tap(find.text('Get started'));
        await t.pumpAndSettle();
        await signInThroughThePhoneFlow(t);
        expect(find.text(entry.value), findsOneWidget);
        expect(await auth.currentSession(), isNull);
      });
    }
  });

  group('role changes while signed in', () {
    testWidgets('a role change on the server re-routes on resume', (t) async {
      final probe = Probe();
      final auth = FakeAuthService()..signInAs(['parent']);
      await t.pumpWidget(host(auth, initial: '/home', probe: probe));
      await t.pumpAndSettle();
      expect(find.text('PROTECTED home'), findsOneWidget);
      auth.labels = ['teacher']; // staff withdrew parent, granted teacher
      t.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await t.pumpAndSettle();
      expect(find.byType(WrongAppScreen), findsOneWidget);
      expect(find.text('PROTECTED home'), findsNothing);
      auth.labels = ['teacher', 'parent']; // and back
      t.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await t.pumpAndSettle();
      expect(find.text('PROTECTED home'), findsOneWidget);
    });

    testWidgets('losing every role shows no access, gaining one admits', (
      t,
    ) async {
      final auth = FakeAuthService()..signInAs(['parent']);
      await t.pumpWidget(host(auth, initial: '/home', probe: Probe()));
      await t.pumpAndSettle();
      auth.labels = [];
      t.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await t.pumpAndSettle();
      expect(find.text('No access with this account'), findsOneWidget);
      auth.labels = ['guardian'];
      t.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await t.pumpAndSettle();
      expect(find.text('PROTECTED home'), findsOneWidget);
    });

    testWidgets('the session being revoked sends the user to sign-in', (
      t,
    ) async {
      final auth = FakeAuthService()..signInAs(['parent']);
      await t.pumpWidget(host(auth, initial: '/home', probe: Probe()));
      await t.pumpAndSettle();
      await auth.signOut(); // revoked server side
      t.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await t.pumpAndSettle();
      expect(find.text('Enter your phone number'), findsOneWidget);
    });

    testWidgets('a failing refresh (offline) keeps the user where they are', (
      t,
    ) async {
      final auth = FlakyAuth()..signInAs(['parent']);
      await t.pumpWidget(host(auth, initial: '/home', probe: Probe()));
      await t.pumpAndSettle();
      auth.failRefresh = true;
      t.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await t.pumpAndSettle();
      expect(find.text('PROTECTED home'), findsOneWidget);
    });
  });

  group('start-up', () {
    testWidgets(
      'a protected deep link is not built before the session is read',
      (t) async {
        final probe = Probe();
        final auth = SlowAuth()..signInAs(['parent']);
        await t.pumpWidget(host(auth, initial: '/billing', probe: probe));
        for (var i = 0; i < 5; i++) {
          await t.pump(const Duration(milliseconds: 50));
          expect(probe.builds, 0, reason: 'frame $i flashed protected content');
        }
        expect(find.byType(StartScreen), findsOneWidget);
        auth.gate.complete();
        await t.pumpAndSettle();
        expect(find.text('PROTECTED billing'), findsOneWidget);
      },
    );

    testWidgets(
      'while loading, a signed-out user never sees protected content',
      (t) async {
        final probe = Probe();
        final auth = SlowAuth(); // nobody signed in
        await t.pumpWidget(host(auth, initial: '/home', probe: probe));
        await t.pump();
        await t.pump(const Duration(milliseconds: 100));
        expect(probe.builds, 0);
        auth.gate.complete();
        await t.pumpAndSettle();
        expect(probe.builds, 0);
        expect(find.text('Enter your phone number'), findsOneWidget);
      },
    );

    testWidgets('a restored wrong-role session never builds the tab', (
      t,
    ) async {
      final probe = Probe();
      final auth = SlowAuth()..signInAs(['teacher']);
      await t.pumpWidget(host(auth, initial: '/home', probe: probe));
      await t.pump();
      auth.gate.complete();
      await t.pumpAndSettle();
      expect(probe.builds, 0);
      expect(find.byType(WrongAppScreen), findsOneWidget);
    });

    testWidgets('an unreadable session store starts signed out', (t) async {
      await t.pumpWidget(
        ProviderScope(
          child: HsApp(
            router: buildAppRouter(AppSpec.parent, initialLocation: '/home'),
          ),
        ),
      );
      await t.pumpAndSettle();
      expect(find.text('Enter your phone number'), findsOneWidget);
    });

    testWidgets('the splash is labelled for screen readers', (t) async {
      final h = t.ensureSemantics();
      final auth = SlowAuth();
      await t.pumpWidget(host(auth));
      await t.pump();
      expect(find.bySemanticsLabel('Loading'), findsOneWidget);
      h.dispose();
      auth.gate.complete();
      await t.pumpAndSettle();
    });
  });

  group('races', () {
    testWidgets('a slow refresh cannot sign the user back in after sign-out', (
      t,
    ) async {
      final auth = SlowAuth()..signInAs(['parent']);
      await t.pumpWidget(host(auth, initial: '/home', probe: Probe()));
      await t.pump();
      final container = ProviderScope.containerOf(
        t.element(find.byType(MaterialApp)),
      );
      final signOut = container.read(sessionProvider.notifier).signOut();
      auth.gate.complete(); // the stale refresh now returns a session
      await signOut;
      await t.pumpAndSettle();
      expect(container.read(sessionProvider).isSignedIn, isFalse);
      expect(find.text('PROTECTED home'), findsNothing);
    });
  });

  group('races 2', () {
    testWidgets(
      'a refresh waiting on the core status cannot sign in after sign-out',
      (t) async {
        final auth = FakeAuthService()..signInAs(['parent']);
        final access = FakeAccess()..hold = Completer<void>();
        await t.pumpWidget(
          host(auth, initial: '/home', probe: Probe(), access: access),
        );
        await t.pump();
        await t.pump();
        final container = ProviderScope.containerOf(
          t.element(find.byType(MaterialApp)),
        );
        await container.read(sessionProvider.notifier).signOut();
        access.hold!.complete();
        await t.pumpAndSettle();
        expect(container.read(sessionProvider).isSignedIn, isFalse);
      },
    );
  });

  group('after sign-out', () {
    testWidgets('the stack is clean: protected routes redirect again', (
      t,
    ) async {
      final probe = Probe();
      final auth = FakeAuthService()..signInAs(['parent']);
      await t.pumpWidget(host(auth, initial: '/billing', probe: probe));
      await t.pumpAndSettle();
      expect(find.text('PROTECTED billing'), findsOneWidget);
      final before = probe.builds;
      await ProviderScope.containerOf(t.element(find.byType(MaterialApp)))
          .read(sessionProvider.notifier)
          .signOut();
      await t.pumpAndSettle();
      expect(find.text('Enter your phone number'), findsOneWidget);
      expect(lastRouter.canPop(), isFalse);
      lastRouter.go('/billing');
      await t.pumpAndSettle();
      expect(find.text('Enter your phone number'), findsOneWidget);
      expect(probe.builds, before, reason: 'no protected build after sign-out');
    });
  });

  group('landing hook (status gating belongs to V03)', () {
    testWidgets('an admitted user lands where the hook says', (t) async {
      final auth = FakeAuthService()..signUpLabels = ['teacher'];
      await t.pumpWidget(
        host(
          auth,
          spec: AppSpec.teacher,
          landing: (s) => '/earnings',
          probe: Probe(),
        ),
      );
      await t.pumpAndSettle();
      await t.tap(find.text('Get started'));
      await t.pumpAndSettle();
      await signInThroughThePhoneFlow(t);
      expect(find.text('PROTECTED earnings'), findsOneWidget);
    });

    testWidgets('a restored session lands on the hook path too', (t) async {
      final auth = FakeAuthService()..signInAs(['teacher']);
      await t.pumpWidget(
        host(
          auth,
          spec: AppSpec.teacher,
          landing: (s) => '/growth',
          probe: Probe(),
        ),
      );
      await t.pumpAndSettle();
      expect(find.text('PROTECTED growth'), findsOneWidget);
    });
  });

  group('accessibility of the new screens', () {
    for (final scale in [1.3, 2.0]) {
      for (final status in [AccountStatus.active, AccountStatus.suspended]) {
        testWidgets(
          '${status == AccountStatus.active ? 'wrong-app' : 'suspended'} '
          'screen at ${(scale * 100).round()}% font',
          (t) async {
            t.view.physicalSize = const Size(360, 640);
            t.view.devicePixelRatio = 1;
            addTearDown(t.view.reset);
            final auth = FakeAuthService()
              ..signInAs(['teacher'], accountStatus: status);
            final spec = status == AccountStatus.active
                ? AppSpec.parent
                : AppSpec.teacher;
            await t.pumpWidget(
              host(
                auth,
                spec: spec,
                initial: '/${spec.tabs.first.path}',
                textScale: scale,
              ),
            );
            await t.pumpAndSettle();
            expect(t.takeException(), isNull);
            final button = find.byType(FilledButton);
            expect(button, findsOneWidget);
            await t.ensureVisible(button);
            expect(t.getSize(button).height, greaterThanOrEqualTo(48));
            expect(t.getSize(button).width, greaterThanOrEqualTo(48));
          },
        );
      }
    }

    testWidgets(
      'title is a header, the button has a label, text from the ARB',
      (t) async {
        final h = t.ensureSemantics();
        final auth = FakeAuthService()..signInAs(['teacher']);
        await t.pumpWidget(host(auth, initial: '/home'));
        await t.pumpAndSettle();
        final s = HsStrings.of(t.element(find.byType(WrongAppScreen)));
        expect(find.text(s.wrongAppTitleParent), findsOneWidget);
        expect(find.text(s.wrongAppToTeacher), findsOneWidget);
        expect(find.text(s.signOut), findsOneWidget);
        final node = t.getSemantics(find.text(s.wrongAppTitleParent));
        expect(node.flagsCollection.isHeader, isTrue);
        expect(find.bySemanticsLabel(s.signOut), findsOneWidget);
        h.dispose();
      },
    );
  });
}
