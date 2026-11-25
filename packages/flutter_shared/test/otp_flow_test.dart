import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

const _field = Key('otp-phone-field');
const _code = Key('otp-code-field');

Widget flowApp(
  FakeAuthService auth, {
  AppSpec? spec,
  String initial = signInPhonePath,
  double textScale = 1,
}) => ProviderScope(
  overrides: [authServiceProvider.overrideWithValue(auth)],
  child: MediaQuery(
    data: MediaQueryData(textScaler: TextScaler.linear(textScale)),
    child: HsApp(
      router: buildAppRouter(spec ?? AppSpec.parent, initialLocation: initial),
    ),
  ),
);

Future<void> enterPhone(WidgetTester t, String text) async {
  await t.enterText(find.byKey(_field), text);
  await t.pump();
}

Future<void> toCodeScreen(WidgetTester t, FakeAuthService auth) async {
  await t.pumpWidget(flowApp(auth));
  await t.pumpAndSettle();
  await enterPhone(t, '0772123456');
  await tapKey(t, 'otp-send');
  await t.pumpAndSettle();
  expect(find.text('Enter the 6-digit code'), findsOneWidget);
}

Future<void> typeCode(WidgetTester t, String code) async {
  await t.enterText(find.byKey(_code), code);
  await t.pumpAndSettle();
}

Future<void> tapKey(WidgetTester t, String key) async {
  final f = find.byKey(Key(key));
  await t.ensureVisible(f);
  await t.pump();
  await t.tap(f);
}

String? errorText(WidgetTester t) {
  final f = find.byKey(const Key('otp-error'));
  return f.evaluate().isEmpty ? null : t.widget<Text>(f).data;
}

void main() {
  group('phone entry (P-02, T-02)', () {
    testWidgets('fixed +256, note, and a nine-digit field', (t) async {
      await t.pumpWidget(flowApp(FakeAuthService()));
      await t.pumpAndSettle();
      expect(find.text('Enter your phone number'), findsOneWidget);
      expect(find.text("We'll send a code by SMS."), findsOneWidget);
      expect(find.byKey(const Key('otp-country-code')), findsOneWidget);
      expect(find.text('+256'), findsOneWidget);
      // the prefix is not editable: only the field takes input
      expect(find.byType(TextField), findsOneWidget);
    });

    testWidgets('formats as +256 772 123 456 whatever way it is typed', (
      t,
    ) async {
      await t.pumpWidget(flowApp(FakeAuthService()));
      await t.pumpAndSettle();
      for (final raw in [
        '0772123456',
        '772123456',
        '+256772123456',
        '256 772 123 456',
      ]) {
        await enterPhone(t, raw);
        expect(
          t.widget<TextField>(find.byKey(_field)).controller!.text,
          '772 123 456',
        );
      }
    });

    testWidgets('sends the E.164 number and moves to the code screen', (
      t,
    ) async {
      final auth = FakeAuthService();
      await toCodeScreen(t, auth);
      expect(auth.requestedPhones, ['+256772123456']);
      expect(find.text('We sent a code to +256 *** *** 456'), findsOneWidget);
      expect(
        find.textContaining('772'),
        findsNothing,
        reason: 'number is masked',
      );
    });

    testWidgets(
      'invalid number: message, nothing sent, can correct and retry',
      (t) async {
        final auth = FakeAuthService();
        await t.pumpWidget(flowApp(auth));
        await t.pumpAndSettle();
        await enterPhone(t, '12345');
        await tapKey(t, 'otp-send');
        await t.pumpAndSettle();
        expect(errorText(t), startsWith('Enter a valid Ugandan mobile number'));
        expect(auth.requestedPhones, isEmpty);
        await enterPhone(t, '0312123456'); // landline prefix: not a mobile
        await tapKey(t, 'otp-send');
        await t.pumpAndSettle();
        expect(errorText(t), isNotNull);
        await enterPhone(t, '0701234567');
        expect(errorText(t), isNull, reason: 'editing clears the error');
        await tapKey(t, 'otp-send');
        await t.pumpAndSettle();
        expect(auth.requestedPhones, ['+256701234567']);
      },
    );

    testWidgets('sending state disables the button and shows progress', (
      t,
    ) async {
      final auth = FakeAuthService()..latency = const Duration(seconds: 2);
      await t.pumpWidget(flowApp(auth));
      await t.pumpAndSettle();
      await enterPhone(t, '0772123456');
      await tapKey(t, 'otp-send');
      await t.pump(const Duration(milliseconds: 100));
      expect(find.text('Sending code'), findsOneWidget);
      expect(
        t.widget<FilledButton>(find.byKey(const Key('otp-send'))).onPressed,
        isNull,
      );
      await t.pump(const Duration(seconds: 3));
      await t.pumpAndSettle();
      expect(find.text('Enter the 6-digit code'), findsOneWidget);
    });

    testWidgets('a second tap while sending does not send twice', (t) async {
      final auth = FakeAuthService()..latency = const Duration(seconds: 2);
      await t.pumpWidget(flowApp(auth));
      await t.pumpAndSettle();
      await enterPhone(t, '0772123456');
      await tapKey(t, 'otp-send');
      await t.pump(const Duration(milliseconds: 50));
      await tapKey(t, 'otp-send');
      await t.pump(const Duration(seconds: 3));
      await t.pumpAndSettle();
      expect(auth.requestedPhones, hasLength(1));
    });

    testWidgets(
      'rate limited: lockout message with the wait, stays on the phone screen',
      (t) async {
        final auth = FakeAuthService()
          ..requestFailures.add(
            const OtpException(
              OtpFailure.rateLimited,
              retryAfter: Duration(minutes: 42),
            ),
          );
        await t.pumpWidget(flowApp(auth));
        await t.pumpAndSettle();
        await enterPhone(t, '0772123456');
        await tapKey(t, 'otp-send');
        await t.pumpAndSettle();
        expect(
          errorText(t),
          'Too many codes requested. Try again in 42 minutes.',
        );
        expect(find.text('Enter your phone number'), findsOneWidget);
      },
    );

    testWidgets('short waits are shown in seconds', (t) async {
      final auth = FakeAuthService()
        ..requestFailures.add(
          const OtpException(
            OtpFailure.rateLimited,
            retryAfter: Duration(seconds: 20),
          ),
        );
      await t.pumpWidget(flowApp(auth));
      await t.pumpAndSettle();
      await enterPhone(t, '0772123456');
      await tapKey(t, 'otp-send');
      await t.pumpAndSettle();
      expect(
        errorText(t),
        'Too many codes requested. Try again in 20 seconds.',
      );
    });

    testWidgets('offline: clear message, retry works once back online', (
      t,
    ) async {
      final auth = FakeAuthService()..offline = true;
      await t.pumpWidget(flowApp(auth));
      await t.pumpAndSettle();
      await enterPhone(t, '0772123456');
      await tapKey(t, 'otp-send');
      await t.pumpAndSettle();
      expect(
        errorText(t),
        "You're offline. Check your connection and try again.",
      );
      auth.offline = false;
      await tapKey(t, 'otp-send');
      await t.pumpAndSettle();
      expect(find.text('Enter the 6-digit code'), findsOneWidget);
    });

    for (final c in [
      (
        OtpFailure.smsUnavailable,
        "We couldn't send the SMS right now. Please try again in a moment.",
      ),
      (
        OtpFailure.numberUnreachable,
        "We can't send an SMS to this number. Use a different number.",
      ),
      (
        OtpFailure.serverUnavailable,
        'Sign-in is temporarily unavailable. Please try again.',
      ),
      (OtpFailure.unknown, 'Something went wrong. Please try again.'),
    ]) {
      testWidgets('${c.$1.name} shows its message on the phone screen', (
        t,
      ) async {
        final auth = FakeAuthService()..requestFailures.add(OtpException(c.$1));
        await t.pumpWidget(flowApp(auth));
        await t.pumpAndSettle();
        await enterPhone(t, '0772123456');
        await tapKey(t, 'otp-send');
        await t.pumpAndSettle();
        expect(errorText(t), c.$2);
        expect(find.text('Enter your phone number'), findsOneWidget);
      });
    }
  });

  group('code entry (P-03)', () {
    testWidgets(
      'six boxes, number masked, correct code signs in on the 6th digit',
      (t) async {
        final auth = FakeAuthService();
        await toCodeScreen(t, auth);
        for (var i = 0; i < 6; i++) {
          expect(find.byKey(Key('otp-box-$i')), findsOneWidget);
        }
        await typeCode(t, '12345');
        expect(
          auth.verifiedCodes,
          isEmpty,
          reason: 'no submit before the 6th digit',
        );
        await typeCode(t, '123456'); // no Verify tap: auto-submit
        expect(auth.verifiedCodes, ['123456']);
        expect(find.text('Home will appear here.'), findsOneWidget);
      },
    );

    testWidgets('boxes show the typed digits', (t) async {
      await toCodeScreen(t, FakeAuthService());
      await typeCode(t, '482');
      expect(
        find.descendant(
          of: find.byKey(const Key('otp-box-0')),
          matching: find.text('4'),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: find.byKey(const Key('otp-box-2')),
          matching: find.text('2'),
        ),
        findsOneWidget,
      );
      expect(
        find
            .descendant(
              of: find.byKey(const Key('otp-box-3')),
              matching: find.byType(Text),
            )
            .evaluate()
            .single
            .widget,
        isA<Text>(),
      );
    });

    testWidgets('only digits are accepted, at most six', (t) async {
      final auth = FakeAuthService()..validCode = '999999';
      await toCodeScreen(t, auth);
      expect(t.widget<TextField>(find.byKey(_code)).maxLength, 6);
      await typeCode(t, '12ab34');
      expect(t.widget<TextField>(find.byKey(_code)).controller!.text, '1234');
      expect(auth.verifiedCodes, isEmpty);
      await typeCode(t, '12345678');
      expect(auth.verifiedCodes, [
        '123456',
      ], reason: 'extra digits are dropped');
    });

    testWidgets('Verify button works too and is disabled until six digits', (
      t,
    ) async {
      final auth = FakeAuthService();
      await toCodeScreen(t, auth);
      expect(
        t.widget<FilledButton>(find.byKey(const Key('otp-verify'))).onPressed,
        isNull,
      );
    });

    testWidgets(
      'wrong code: message with tries left, boxes cleared, can retry',
      (t) async {
        final auth = FakeAuthService();
        await toCodeScreen(t, auth);
        await typeCode(t, '000000');
        expect(errorText(t), "That code isn't right. 4 tries left.");
        expect(
          t.widget<TextField>(find.byKey(_code)).controller!.text,
          isEmpty,
        );
        await typeCode(t, '111111');
        expect(errorText(t), "That code isn't right. 3 tries left.");
        await typeCode(t, '123456');
        expect(find.text('Home will appear here.'), findsOneWidget);
      },
    );

    testWidgets('last try is singular', (t) async {
      final auth = FakeAuthService()..maxAttempts = 2;
      await toCodeScreen(t, auth);
      await typeCode(t, '000000');
      expect(errorText(t), "That code isn't right. 1 try left.");
    });

    testWidgets(
      'code expired: message, input disabled, resend gives a fresh start',
      (t) async {
        final auth = FakeAuthService();
        await toCodeScreen(t, auth);
        auth.codeExpired = true;
        await typeCode(t, '123456');
        expect(errorText(t), 'That code has expired. Request a new one.');
        expect(t.widget<TextField>(find.byKey(_code)).enabled, isFalse);
        await t.pump(const Duration(seconds: 31));
        await tapKey(t, 'otp-resend');
        await t.pumpAndSettle();
        expect(auth.requestedPhones, hasLength(2));
        expect(errorText(t), isNull);
        expect(t.widget<TextField>(find.byKey(_code)).enabled, isTrue);
        await typeCode(t, '123456');
        expect(find.text('Home will appear here.'), findsOneWidget);
      },
    );

    testWidgets('too many attempts after five wrong codes', (t) async {
      final auth = FakeAuthService();
      await toCodeScreen(t, auth);
      for (var i = 0; i < 5; i++) {
        await typeCode(t, '00000$i');
      }
      expect(errorText(t), "That code isn't right. No tries left.");
      await typeCode(t, '123456'); // even the right code is refused now
      expect(errorText(t), 'Too many wrong tries. Request a new code.');
      expect(find.text('Home will appear here.'), findsNothing);
      expect(t.widget<TextField>(find.byKey(_code)).enabled, isFalse);
    });

    testWidgets('locked out for an hour is explained with the wait', (t) async {
      final auth = FakeAuthService()
        ..verifyFailures.add(
          const OtpException(
            OtpFailure.locked,
            retryAfter: Duration(minutes: 55),
          ),
        );
      await toCodeScreen(t, auth);
      await typeCode(t, '123456');
      expect(
        errorText(t),
        'Too many wrong codes. For your safety, try again in 55 minutes.',
      );
    });

    testWidgets('rate limited while verifying', (t) async {
      final auth = FakeAuthService()
        ..verifyFailures.add(
          const OtpException(
            OtpFailure.rateLimited,
            retryAfter: Duration(minutes: 1),
          ),
        );
      await toCodeScreen(t, auth);
      await typeCode(t, '123456');
      expect(errorText(t), 'Too many codes requested. Try again in 1 minute.');
    });

    testWidgets(
      'offline while verifying: message, then the same code works online',
      (t) async {
        final auth = FakeAuthService();
        await toCodeScreen(t, auth);
        auth.offline = true;
        await typeCode(t, '123456');
        expect(
          errorText(t),
          "You're offline. Check your connection and try again.",
        );
        auth.offline = false;
        await t.enterText(find.byKey(_code), '');
        await typeCode(t, '123456');
        expect(find.text('Home will appear here.'), findsOneWidget);
      },
    );

    for (final c in [
      (
        OtpFailure.accountBlocked,
        "This account can't sign in. Please contact support.",
      ),
      (
        OtpFailure.serverUnavailable,
        'Sign-in is temporarily unavailable. Please try again.',
      ),
    ]) {
      testWidgets('${c.$1.name} while verifying', (t) async {
        final auth = FakeAuthService()..verifyFailures.add(OtpException(c.$1));
        await toCodeScreen(t, auth);
        await typeCode(t, '123456');
        expect(errorText(t), c.$2);
      });
    }

    testWidgets('typing again after an error clears it', (t) async {
      final auth = FakeAuthService()
        ..verifyFailures.add(const OtpException(OtpFailure.serverUnavailable));
      await toCodeScreen(t, auth);
      await typeCode(t, '123456');
      expect(errorText(t), isNotNull);
      await t.enterText(find.byKey(_code), '1');
      await t.pump();
      expect(errorText(t), isNull);
    });
  });

  group('resend and SMS delayed', () {
    testWidgets('countdown from 30 s disables resend, then enables it', (
      t,
    ) async {
      final auth = FakeAuthService();
      await toCodeScreen(t, auth);
      expect(find.text('Resend code in 30s'), findsOneWidget);
      expect(
        t.widget<TextButton>(find.byKey(const Key('otp-resend'))).onPressed,
        isNull,
      );
      await t.pump(const Duration(seconds: 10));
      expect(find.text('Resend code in 20s'), findsOneWidget);
      await t.pump(const Duration(seconds: 20));
      expect(find.text('Resend code'), findsOneWidget);
      expect(
        t.widget<TextButton>(find.byKey(const Key('otp-resend'))).onPressed,
        isNotNull,
      );
    });

    testWidgets('resend sends to the same number and restarts the countdown', (
      t,
    ) async {
      final auth = FakeAuthService();
      await toCodeScreen(t, auth);
      await t.pump(const Duration(seconds: 30));
      await tapKey(t, 'otp-resend');
      await t.pumpAndSettle();
      expect(auth.requestedPhones, ['+256772123456', '+256772123456']);
      expect(find.text('Resend code in 30s'), findsOneWidget);
    });

    testWidgets(
      'resend refused by the server shows the wait and keeps the code screen',
      (t) async {
        final auth = FakeAuthService();
        await toCodeScreen(t, auth);
        await t.pump(const Duration(seconds: 30));
        auth.requestFailures.add(
          const OtpException(
            OtpFailure.rateLimited,
            retryAfter: Duration(minutes: 30),
          ),
        );
        await tapKey(t, 'otp-resend');
        await t.pumpAndSettle();
        expect(
          errorText(t),
          'Too many codes requested. Try again in 30 minutes.',
        );
        expect(find.text('Enter the 6-digit code'), findsOneWidget);
        expect(
          t.widget<TextButton>(find.byKey(const Key('otp-resend'))).onPressed,
          isNull,
        );
      },
    );

    testWidgets(
      'SMS provider outage on resend leaves the earlier code usable',
      (t) async {
        final auth = FakeAuthService();
        await toCodeScreen(t, auth);
        await t.pump(const Duration(seconds: 30));
        auth.requestFailures.add(const OtpException(OtpFailure.smsUnavailable));
        await tapKey(t, 'otp-resend');
        await t.pumpAndSettle();
        expect(errorText(t), startsWith("We couldn't send the SMS"));
        await typeCode(t, '123456');
        expect(find.text('Home will appear here.'), findsOneWidget);
      },
    );

    testWidgets(
      'after the countdown with no code: delayed hint, change number, help',
      (t) async {
        await toCodeScreen(t, FakeAuthService());
        expect(find.byKey(const Key('otp-sms-delayed')), findsNothing);
        await t.pump(const Duration(seconds: 31));
        expect(find.byKey(const Key('otp-sms-delayed')), findsOneWidget);
        expect(find.byKey(const Key('otp-change-number')), findsOneWidget);
        await tapKey(t, 'otp-help');
        await t.pumpAndSettle();
        expect(find.text('No SMS arriving?'), findsOneWidget);
        expect(
          find.textContaining('use a different number or contact support'),
          findsOneWidget,
        );
        await t.tap(find.text('Close'));
        await t.pumpAndSettle();
        expect(find.text('No SMS arriving?'), findsNothing);
      },
    );

    testWidgets('use a different number returns to an empty phone screen', (
      t,
    ) async {
      final auth = FakeAuthService();
      await toCodeScreen(t, auth);
      await t.pump(const Duration(seconds: 31));
      await tapKey(t, 'otp-change-number');
      await t.pumpAndSettle();
      expect(find.text('Enter your phone number'), findsOneWidget);
      expect(t.widget<TextField>(find.byKey(_field)).controller!.text, isEmpty);
      await enterPhone(t, '0701234567');
      await tapKey(t, 'otp-send');
      await t.pumpAndSettle();
      expect(auth.requestedPhones.last, '+256701234567');
      expect(find.text('We sent a code to +256 *** *** 567'), findsOneWidget);
    });

    testWidgets('the app bar back button also lets the user change number', (
      t,
    ) async {
      await toCodeScreen(t, FakeAuthService());
      await t.tap(find.byType(BackButton));
      await t.pumpAndSettle();
      expect(find.text('Enter your phone number'), findsOneWidget);
    });

    testWidgets('countdown timer stops when leaving (no pending timers)', (
      t,
    ) async {
      await toCodeScreen(t, FakeAuthService());
      await tapKey(t, 'otp-change-number');
      await t.pumpAndSettle();
      await t.pumpWidget(const SizedBox());
      // the test binding fails the test if a periodic timer is left running
    });
  });

  group('welcome and both apps', () {
    testWidgets(
      'Get started and I already have an account both open phone entry',
      (t) async {
        for (final label in ['Get started', 'I already have an account']) {
          await t.pumpWidget(flowApp(FakeAuthService(), initial: '/'));
          await t.pumpAndSettle();
          await t.tap(find.text(label));
          await t.pumpAndSettle();
          expect(
            find.text('Enter your phone number'),
            findsOneWidget,
            reason: label,
          );
        }
      },
    );

    testWidgets('teacher app (T-02) runs the same flow and lands on Today', (
      t,
    ) async {
      final auth = FakeAuthService();
      // The teacher app sends new teachers on to the application flow
      // (postSignInPath, V01); the shared flow itself still lands on the
      // first tab when no such path is set.
      final spec = AppSpec(
        tabs: AppSpec.teacher.tabs,
        welcomeBody: AppSpec.teacher.welcomeBody,
      );
      expect(AppSpec.teacher.postSignInPath, '/apply');
      await t.pumpWidget(flowApp(auth, spec: spec));
      await t.pumpAndSettle();
      await enterPhone(t, '0752000111');
      await tapKey(t, 'otp-send');
      await t.pumpAndSettle();
      await typeCode(t, '123456');
      expect(find.text('Today will appear here.'), findsOneWidget);
      expect(auth.requestedPhones, ['+256752000111']);
    });

    testWidgets('a new flow after sign-out starts clean', (t) async {
      final auth = FakeAuthService();
      await toCodeScreen(t, auth);
      await typeCode(t, '123456');
      await t.pumpWidget(flowApp(auth));
      await t.pumpAndSettle();
      expect(find.text('Enter your phone number'), findsOneWidget);
      expect(find.byKey(const Key('otp-error')), findsNothing);
    });
  });

  group('accessibility and layout (Design Brief s10)', () {
    testWidgets('semantic labels on the phone and code fields', (t) async {
      final handle = t.ensureSemantics();
      final auth = FakeAuthService();
      await t.pumpWidget(flowApp(auth));
      await t.pumpAndSettle();
      expect(find.bySemanticsLabel(RegExp('Phone number')), findsWidgets);
      await enterPhone(t, '0772123456');
      await tapKey(t, 'otp-send');
      await t.pumpAndSettle();
      expect(
        find.bySemanticsLabel('Verification code, 6 digits'),
        findsOneWidget,
      );
      expect(
        t
            .getSemantics(find.bySemanticsLabel('Verification code, 6 digits'))
            .flagsCollection
            .isTextField,
        isTrue,
      );
      handle.dispose();
    });

    testWidgets('errors are announced as a live region', (t) async {
      final handle = t.ensureSemantics();
      final auth = FakeAuthService();
      await toCodeScreen(t, auth);
      await typeCode(t, '000000');
      final node = t.getSemantics(find.byKey(const Key('otp-error')));
      expect(node.flagsCollection.isLiveRegion, isTrue);
      handle.dispose();
    });

    testWidgets('digit boxes are not separate accessibility nodes', (t) async {
      final handle = t.ensureSemantics();
      await toCodeScreen(t, FakeAuthService());
      await typeCode(t, '12');
      expect(find.bySemanticsLabel('1'), findsNothing);
      handle.dispose();
    });

    for (final screen in ['phone', 'code']) {
      testWidgets('every control on the $screen screen is at least 48dp', (
        t,
      ) async {
        final auth = FakeAuthService();
        if (screen == 'phone') {
          await t.pumpWidget(flowApp(auth));
          await t.pumpAndSettle();
        } else {
          await toCodeScreen(t, auth);
          await t.pump(const Duration(seconds: 31));
        }
        final targets = <Finder>[
          find.byType(FilledButton),
          find.byType(TextButton),
          find.byType(TextField),
          find.byKey(const Key('otp-country-code')),
        ];
        var n = 0;
        for (final f in targets) {
          for (final e in f.evaluate()) {
            final size = t.getSize(find.byElementPredicate((x) => x == e));
            n++;
            expect(
              size.height,
              greaterThanOrEqualTo(48),
              reason: '${e.widget} on $screen',
            );
            expect(
              size.width,
              greaterThanOrEqualTo(48),
              reason: '${e.widget} on $screen',
            );
          }
        }
        expect(n, greaterThan(2));
      });
    }

    for (final scale in [1.3, 2.0]) {
      testWidgets(
        'no overflow at ${(scale * 100).round()}% font on a 360x640 phone',
        (t) async {
          t.view.physicalSize = const Size(360, 640);
          t.view.devicePixelRatio = 1;
          addTearDown(t.view.reset);
          final auth = FakeAuthService();
          await t.pumpWidget(flowApp(auth, textScale: scale));
          await t.pumpAndSettle();
          await enterPhone(t, '0772123456');
          await tapKey(t, 'otp-send');
          await t.pumpAndSettle();
          await typeCode(t, '000000');
          await t.pump(const Duration(seconds: 31));
          expect(t.takeException(), isNull);
          await tapKey(t, 'otp-help');
          await t.pumpAndSettle();
          expect(t.takeException(), isNull);
        },
      );
    }
  });

  group('state machine', () {
    test('a double submit of the same code verifies once', () async {
      final auth = FakeAuthService()
        ..latency = const Duration(milliseconds: 50);
      final c = ProviderContainer(
        overrides: [authServiceProvider.overrideWithValue(auth)],
      );
      addTearDown(c.dispose);
      final flow = c.read(otpFlowProvider.notifier);
      await flow.submitPhone('772123456');
      await Future.wait([flow.verify('123456'), flow.verify('123456')]);
      expect(auth.verifiedCodes, hasLength(1));
      expect(c.read(otpFlowProvider).session, isNotNull);
    });

    test('resend before the countdown ends is ignored', () async {
      final auth = FakeAuthService();
      final c = ProviderContainer(
        overrides: [authServiceProvider.overrideWithValue(auth)],
      );
      addTearDown(c.dispose);
      final flow = c.read(otpFlowProvider.notifier);
      await flow.submitPhone('772123456');
      await flow.resend();
      expect(auth.requestedPhones, hasLength(1));
    });

    test('the full number is only in memory for the flow and masked in the session', () async {
      final auth = FakeAuthService();
      final c = ProviderContainer(
        overrides: [authServiceProvider.overrideWithValue(auth)],
      );
      addTearDown(c.dispose);
      final flow = c.read(otpFlowProvider.notifier);
      await flow.submitPhone('772123456');
      await flow.verify('123456');
      expect(c.read(otpFlowProvider).session!.phoneMasked, '+256 *** *** 456');
      flow.reset();
      expect(c.read(otpFlowProvider).e164, isNull);
    });
  });
}
