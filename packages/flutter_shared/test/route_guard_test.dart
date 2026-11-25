import 'package:flutter_test/flutter_test.dart';
import 'package:home_school_shared/home_school_shared.dart';

String? go(
  SessionState s,
  String location, {
  AppId app = AppId.parent,
  String home = '/home',
}) => guardRedirect(
  app: app,
  session: s,
  location: Uri.parse(location),
  homePath: home,
);

SessionState signedIn(
  List<String> labels, [
  AccountStatus st = AccountStatus.active,
]) =>
    SessionState.signedIn(AuthSession(userId: 'u', labels: labels, status: st));

const protected = [
  '/home',
  '/sessions',
  '/progress',
  '/billing',
  '/account',
  '/today',
  '/apply',
  '/anything/else',
];

void main() {
  group('signed out', () {
    test('every protected path goes to the phone screen, remembering it', () {
      for (final p in protected) {
        final r = go(const SessionState.signedOut(), p)!;
        expect(Uri.parse(r).path, signInPhonePath, reason: p);
        expect(Uri.parse(r).queryParameters['from'], p, reason: p);
      }
    });
    test('public screens are open', () {
      for (final p in [
        welcomePath,
        signInPhonePath,
        signInCodePath,
        suspendedPath,
      ]) {
        expect(go(const SessionState.signedOut(), p), isNull, reason: p);
      }
    });
    test('the splash leads on to the welcome screen', () {
      expect(go(const SessionState.signedOut(), startPath), welcomePath);
    });
    test('a held deep link goes straight to the phone screen', () {
      final r = go(const SessionState.signedOut(), '/start?from=%2Fbilling')!;
      expect(Uri.parse(r).path, signInPhonePath);
      expect(Uri.parse(r).queryParameters['from'], '/billing');
    });
    test('the wrong-app screen is not for signed-out users', () {
      expect(go(const SessionState.signedOut(), wrongAppPath), welcomePath);
    });
    test('query strings and fragments cannot smuggle a protected path', () {
      expect(
        go(const SessionState.signedOut(), '/sign-in/phone?x=/home'),
        isNull,
      );
      expect(go(const SessionState.signedOut(), '/'), isNull);
      expect(
        Uri.parse(go(const SessionState.signedOut(), '/home?tab=2')!).path,
        signInPhonePath,
      );
    });
  });

  group('loading', () {
    test('protected paths are held at the splash (nothing flashes)', () {
      for (final p in protected) {
        final r = go(const SessionState.loading(), p)!;
        expect(Uri.parse(r).path, startPath, reason: p);
        expect(Uri.parse(r).queryParameters['from'], p, reason: p);
      }
    });
    test('the splash and the sign-in screens stay; welcome is held', () {
      expect(go(const SessionState.loading(), startPath), isNull);
      expect(go(const SessionState.loading(), signInPhonePath), isNull);
      expect(go(const SessionState.loading(), welcomePath), startPath);
    });
  });

  group('signed in, admitted', () {
    test('auth screens and the splash lead home, or to a safe from', () {
      final s = signedIn(['parent']);
      for (final p in [
        welcomePath,
        signInPhonePath,
        signInCodePath,
        startPath,
      ]) {
        expect(go(s, p), '/home', reason: p);
      }
      expect(go(s, '/sign-in/phone?from=%2Fbilling'), '/billing');
      expect(go(s, '/start?from=%2Fbilling'), '/billing');
    });
    test('protected paths are open; the info screens lead home', () {
      final s = signedIn(['parent']);
      for (final p in protected) {
        expect(go(s, p), isNull, reason: p);
      }
      expect(go(s, wrongAppPath), '/home');
      expect(go(s, suspendedPath), '/home');
    });
    test('guardian and a mixed user are admitted', () {
      expect(go(signedIn(['guardian']), '/billing'), isNull);
      expect(
        go(signedIn(['parent', 'teacher']), '/today', app: AppId.teacher),
        isNull,
      );
      expect(go(signedIn(['parent', 'teacher']), '/billing'), isNull);
    });
    test('unsafe from values are ignored', () {
      final s = signedIn(['parent']);
      for (final bad in [
        '//evil.example',
        'https://evil.example/x',
        'javascript:alert(1)',
        '/sign-in/phone',
        '/wrong-app',
        '/start',
        r'/\evil',
        '/ x',
        '',
        '/${'a' * 300}',
      ]) {
        final enc = Uri.encodeQueryComponent(bad);
        expect(go(s, '/sign-in/phone?from=$enc'), '/home', reason: bad);
      }
    });
    test('the landing path hook is what home means', () {
      expect(
        go(
          signedIn(['teacher']),
          welcomePath,
          app: AppId.teacher,
          home: '/apply',
        ),
        '/apply',
      );
    });
  });

  group('signed in, not admitted', () {
    test('wrong role: everything leads to the wrong-app screen only', () {
      final s = signedIn(['teacher']);
      for (final p in [...protected, welcomePath, signInPhonePath, startPath]) {
        expect(go(s, p), wrongAppPath, reason: p);
      }
      expect(go(s, wrongAppPath), isNull);
    });
    test('staff and unlabelled users too', () {
      for (final labels in [
        ['opsadmin'],
        <String>[],
        ['ops_admin'],
      ]) {
        expect(go(signedIn(labels), '/home'), wrongAppPath, reason: '$labels');
      }
    });
    test('suspended: everything leads to the suspended screen only', () {
      final s = signedIn(['teacher'], AccountStatus.suspended);
      for (final p in [...protected, welcomePath, startPath, wrongAppPath]) {
        expect(go(s, p, app: AppId.teacher), suspendedPath, reason: p);
      }
      expect(go(s, suspendedPath, app: AppId.teacher), isNull);
    });
    test('deactivated has no access', () {
      expect(
        go(signedIn(['parent'], AccountStatus.deactivated), '/home'),
        wrongAppPath,
      );
    });
  });

  test('safeFrom accepts only plain protected in-app paths', () {
    expect(safeFrom('/billing'), '/billing');
    expect(safeFrom('/sessions/2026-11-26'), '/sessions/2026-11-26');
    expect(safeFrom(null), isNull);
    expect(safeFrom('billing'), isNull);
  });

  test('a path is protected unless it is listed public (deny by default)', () {
    expect(isProtectedPath('/brand-new-feature'), isTrue);
    expect(isProtectedPath(welcomePath), isFalse);
  });
}
