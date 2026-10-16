# iOS Share Extension

Adds **LinkVault** to the iPhone share sheet. The extension posts the shared link
straight to your server, so it works even when the app isn't running.

Needs a Mac with Xcode and an Apple Developer account (free provisioning works for
7-day installs on your own phone; $99/year for TestFlight / App Store).

Until you have this set up, use the Apple Shortcut in `extras/ios-shortcut.md`.

## Steps

1. Build the app once on the Mac: `cd app && flet build ipa`.
2. Open `app/build/flutter/ios/Runner.xcworkspace` in Xcode.
3. **File > New > Target… > Share Extension**. Name it `LinkVaultShare`, language Swift.
   When Xcode asks to activate the scheme, choose *Activate*.
4. Replace the generated `ShareViewController.swift` with the one in this folder.
   Delete the generated `MainInterface.storyboard` and remove the
   `NSExtensionMainStoryboard` key from the target's Info.plist.
5. Merge `Info.plist.snippet.xml` into the extension's Info.plist and fill in
   `LVServerURL` and `LVApiKey` (copy the key from LinkVault > Settings).
6. Set the extension's **Deployment target** to iOS 15+ and give it the same Team as
   the Runner target. Bundle ID: `com.linkvault.linkvault.share` (Runner's ID + `.share`).
7. Build and run on your phone. Open Instagram, TikTok or Safari, tap **Share**, scroll
   the app row to the end, tap **More** and enable **LinkVault**.

`flet build ipa` regenerates the Xcode project only when `[tool.flet]` settings change,
so the extension target survives normal rebuilds. If it gets wiped, repeat steps 3–6.

The server URL must be **https** (App Transport Security blocks plain http).
