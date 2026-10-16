# Android share target

Makes **LinkVault** appear in Android's share sheet (Instagram, TikTok, YouTube, Chrome…).

Flet's build has no setting for a `SEND` intent-filter, so we patch the Flutter project
that `flet build` generates:

```bash
cd app
flet build apk                                        # 1. generates build/flutter
python ../native/android/patch_share_intent.py build/flutter   # 2. adds the share target
flet build apk                                        # 3. rebuild
```

What the patch does:
- `AndroidManifest.xml`: adds an `intent-filter` for `android.intent.action.SEND` / `text/plain`.
- `MainActivity.kt`: rewrites the incoming share into the deep link
  `linkvault://app/save?text=<shared text>`. Flet delivers that to Python as the route
  `/save?text=…`, and `share_handler.url_from_route()` picks it up and saves the link.

The deep-link scheme comes from `[tool.flet.deep_linking]` in `app/pyproject.toml`.

Re-run step 2 whenever you change `[tool.flet]` settings, because that makes Flet
regenerate the Flutter project.

**Server URL:** release builds block plain `http://`. Use your deployed `https://` server.
For local testing on the emulator, the app's default server is `http://10.0.2.2:8000`
(your PC's localhost), which works in debug builds.
