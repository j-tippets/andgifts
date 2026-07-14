# &Gifts — Mobile (Capacitor)

This wraps the live &Gifts web app in a native iOS/Android shell using
[Capacitor](https://capacitorjs.com). It's a **remote-loading** wrapper,
not a rebuild: the app opens straight to your production Flask site (see
`server.url` in `capacitor.config.json`), so the existing Jinja2 templates,
sessions, auth, and the mobile-first UI you already built (bottom nav,
swipe cards, etc.) all work unchanged. No parallel API layer needed for v1.

## Decisions to make before this is submittable

1. **Bundle ID / `appId`** (`capacitor.config.json`) — currently a
   placeholder (`com.andgifts.app`). This is registered with Apple and
   Google and is effectively **permanent** once you ship — changing it
   later means a new app listing, not an update. Decide which entity owns
   the app long-term (Wyld Totems? a dedicated &Gifts entity?) before you
   create the developer accounts below, so the identifier matches.
2. **Production domain** (`server.url` in `capacitor.config.json`) —
   there's no custom domain wired up yet (nothing in `config.py` or
   `.do/app.yaml`). Get one pointed at the DigitalOcean app and set it
   here. A raw `*.ondigitalocean.app` URL works for local testing but
   looks unpolished to reviewers and isn't guaranteed stable if the DO
   app is ever recreated.
3. **App icon / splash art** — `resources/icon.png` and
   `resources/splash.png` are placeholders I generated from your brand
   colors (plum/gold, Fraunces-adjacent serif) so the scaffold has
   *something* real installed. Swap in real artwork (1024x1024 icon,
   2732x2732 splash) when you have it, then re-run `npm run assets`.

## Developer accounts

- **Apple Developer Program** — $99/yr. Enrolling as an *individual* is
  usually approved in a day or two. Enrolling as an *organization* (e.g.
  under an LLC) requires Apple to verify a D-U-N-S number, which can take
  a week or more — start this early if you're going the org route.
  https://developer.apple.com/programs/enroll/
- **Google Play Console** — $25 one-time, usually approved within a day.
  https://play.google.com/console/signup

Neither is needed to build/test locally — only to submit to the stores.

## One-time setup (on your machine, not in a sandbox)

```bash
cd mobile
npm install
```

The `android/` and `ios/` platform folders are already generated and
committed (including icon/splash assets), so you shouldn't need
`npx cap add ios/android` again unless you delete them. If you ever do:

```bash
npx cap add android
npx cap add ios
npm run assets       # regenerates icons/splash from resources/ into both platforms
```

After pulling changes or editing `capacitor.config.json`:

```bash
npm run sync         # copies config + web assets into both native projects
```

### Android

```bash
npm run open:android   # opens android/ in Android Studio
```
Requires Android Studio (free). Run on an emulator or a plugged-in device
via the Run button. Signing/release builds are handled through Android
Studio's Build > Generate Signed Bundle flow once you're ready to submit.

### iOS

```bash
npm run open:ios       # opens ios/App/App.xcworkspace in Xcode
```
Requires a Mac with Xcode. First run needs CocoaPods installed
(`sudo gem install cocoapods`) and `pod install` run once inside `ios/App`
if Xcode doesn't do it automatically. Signing requires your Apple
Developer account to be added in Xcode > Settings > Accounts.

## What's deliberately NOT done yet

- **Push notifications** — the `@capacitor/push-notifications` plugin is
  installed but not wired up. This would be genuinely useful for the
  Today screen (nudge agents when new suggestions land overnight), but
  needs APNs (Apple) + FCM (Google) credentials and a send-side
  integration in the Flask app first. Worth doing as its own pass.
- **App Store / Play Store listing assets** — screenshots, descriptions,
  privacy policy URL, content rating questionnaire. Store requirements,
  not code; can be done in parallel with the above.
- **Deep linking** (e.g. a gift-approval link opening straight into the
  app) — possible with Capacitor but not configured.
- **Offline support** — this is a remote-loading wrapper by design; if
  the phone has no connection, the app shows whatever the browser engine
  shows for a failed navigation. Full offline would mean rethinking the
  app as API-first, which is a much bigger project.
