# LinkVault

Share a link from Instagram, TikTok, YouTube, X, Reddit, Facebook, LinkedIn, Pinterest
or any website into LinkVault. The server reads the title, description and thumbnail,
picks a category (Recipes, Tech, Fitness, Travel…), and the app lets you browse and
search everything on Windows, Android and iPhone.

```
backend/   FastAPI server: saves links, extracts metadata, categorizes (rules + Claude)
app/       Flet app (Windows / Android / iOS): browse, search, review, settings, offline cache
native/    Share-sheet integration: Android intent patch, iOS Share Extension (Swift)
extras/    Apple Shortcut guide, Windows tray hotkey, Chrome/Edge extension
```

## 1. Run the backend

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"                       # add ",transcribe" for optional speech-to-text
copy .env.example .env                                 # then edit it: SECRET_KEY, ANTHROPIC_API_KEY
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs for the interactive API.

Without `ANTHROPIC_API_KEY` it still works, using keyword/domain rules only.
With a key, Claude Haiku 5.5 picks the category for unclear links and writes the
one-line description.

Tests: `pytest` (55 tests, no network needed).

### Summaries

Every saved link gets a **Summary** under its description in the app: a short overview
plus bullet points with the main points, steps, tips or ingredients, so you don't have to
rewatch the video.

- **Videos:** the summary is written from the video's captions (the uploader's subtitles,
  else the platform's auto-captions) plus its caption/description. This takes seconds.
- **Articles:** written from the page text.
- Claude Haiku writes the summary in the same call that picks the category, so it costs
  nothing extra in time. Without `ANTHROPIC_API_KEY`, you get a *quick summary* instead:
  the most representative sentences, picked locally.

Summaries are searchable. Videos with no captions are summarized from their
title/description only. To cover those too, `pip install faster-whisper` and set
`ENABLE_WHISPER=true`: the server then transcribes the audio locally (about a minute per
10 minutes of video) and writes the summary from that.

### Screenshots

There's a **Screenshots** category. On Windows, every new screenshot (Win+PrtScn, or
Win+Shift+S / Snipping Tool, which save to `Pictures\Screenshots` or
`OneDrive\Pictures\Screenshots`) is uploaded there automatically while the app or the tray
app is running. Only screenshots taken after you turn it on are sent; older ones are never
uploaded. Switch it off in **Settings > Screenshots**.

You can also add images by hand: the 🖼 button next to the paste box (works on phones too:
it opens your gallery), or the **+** button when a snip is on the clipboard.

Each screenshot gets its text read (searchable, copyable). With Claude it also gets a real
title, a one-line description and a summary. Without Claude, text is read locally with OCR
(`pip install -e ".[ocr]"`).

## 2. Run the app

```bash
cd app
pip install "flet[all]==1.0.4" httpx
flet run                       # Windows desktop window
flet run --android             # live on your phone through the Flet app (scan the QR code)
```

On first launch: enter the server URL, an email and a password, and tap
**Create account**. Then paste a link or press the **+** button to save what's on the
clipboard.

## 3. Save links from other apps

| Where | How | Setup |
|---|---|---|
| iPhone, day one | Apple Shortcut in the share sheet | [extras/ios-shortcut.md](extras/ios-shortcut.md) |
| iPhone, native | Share Extension | [native/ios-share-extension/](native/ios-share-extension/README.md) (needs a Mac) |
| Android | "Share > LinkVault" | [native/android/](native/android/README.md) |
| Windows | App offers to save a copied link when focused; tray hotkey **Ctrl+Shift+S** | [extras/windows_tray.py](extras/windows_tray.py) |
| Any browser | Toolbar button / right-click | [extras/browser-extension/](extras/browser-extension/README.md) |

Your API key for the Shortcut, extension and tray app is in **Settings > API key**.

## 4. Deploy

### Option A: your Windows PC, live whenever it's on (how this setup runs)

The PC runs the server; **Tailscale Funnel** gives it a permanent public `https` address;
a **startup task** launches the server at boot, before anyone logs in.

Run the server with the vault web page included (`serve.py` = API + the app at `/app`):

```bash
.venv\Scripts\python -m uvicorn serve:app --host 127.0.0.1 --port 8000
```

| Address | What it is |
|---|---|
| `https://<pc>.<tailnet>.ts.net/app/` | Your vault in any browser. On iPhone: Safari > Share > **Add to Home Screen** |
| `https://<pc>.<tailnet>.ts.net/s/<api key>/<link>` | Save a link (iPhone Shortcut) |
| `https://<pc>.<tailnet>.ts.net/u/<api key>` | Upload a screenshot: POST, body = the image (iPhone Shortcut) |

Each browser has to log in; nobody gets in just by knowing the address.

#### Add: start with Windows

1. **Public address (once).** Install [Tailscale](https://tailscale.com/download) and sign in.
   Then run:
   ```powershell
   tailscale funnel --bg 8000
   ```
   (If `tailscale` isn't recognised, use `& "C:\Program Files\Tailscale\tailscale.exe" funnel --bg 8000`.)
   The first time, it prints a link: open it and click **Enable** (it also turns on HTTPS).
   Run the command again; it prints your address, e.g. `https://my-pc.tail1234ab.ts.net`.
   Tailscale starts with Windows and remembers this, so it's a one-time step. A brand-new
   address can take a few minutes before phones and browsers can find it.
2. **Start the server at boot.** Right-click `scripts\install-startup.ps1` > **Run with
   PowerShell** and click **Yes** when Windows asks for permission. It registers the task
   **LinkVault Server** (runs as SYSTEM at boot, restarts it if it ever stops) and starts it now.
3. **Keep the PC awake** while plugged in, or phones can't reach it while it sleeps:
   **Settings > System > Power & battery > Screen and sleep > "When plugged in, put my device to
   sleep after": Never**.

The server writes its log to `backend\server.log`. Close the sign-up form for strangers with
`ALLOW_REGISTRATION=false` in `backend\.env`.

#### Remove

- **Stop starting with Windows** (and stop the server now): right-click
  `scripts\uninstall-startup.ps1` > **Run with PowerShell** > **Yes**. Your bookmarks,
  screenshots and settings are kept.
- **Turn off the public address:** `tailscale funnel --https=443 off`
  (`tailscale funnel status` shows whether it's on).
- **Restart the server** after changing code or `.env`: right-click
  `scripts\restart-server.ps1` > **Run with PowerShell** > **Yes**, or simply restart the PC.

### Option B: a cloud server

The backend ships a `Dockerfile`. On Railway, Render or Fly.io: create a project from
the repo with root directory `backend`, add a Postgres database, and set the env vars
from `.env.example` (`DATABASE_URL` is usually filled in for you, and both `postgres://`
and `postgresql://` URLs are accepted). HTTPS is automatic there.

On your own server: `docker compose up -d` (see `docker-compose.yml`) behind a TLS proxy.

After creating your account, set `ALLOW_REGISTRATION=false`.

## 5. Build installers

```bash
cd app
flet build windows     # .exe in build/windows
flet build apk         # then apply native/android/patch_share_intent.py and build again
flet build ipa         # on a Mac; then add the Share Extension in Xcode
```

## How it works

1. `POST /bookmarks {url}` stores the link at once (status `processing`) and returns.
   Tracking parameters (`utm_*`, `igsh`, `si`…) are stripped so re-sharing a link
   bumps it to the top instead of duplicating it.
2. In the background: short links are resolved, then metadata comes from
   oEmbed (YouTube, TikTok, X, Reddit, Instagram/Facebook with a Meta token) >
   OpenGraph tags > yt-dlp (video captions and hashtags) > trafilatura (article text).
3. Categorizing: domain rules (github.com > Tech) and hashtag/keyword rules first, then
   Claude for the description and any unclear category. Low-confidence links land in the
   **Review** tab; Claude can suggest a new category, which you accept with one tap.
4. **Recipes:** any link in a recipe category gets the full recipe (ingredients + method):
   from the recipe website's own data, else written out by Claude from the caption and video
   captions, else read from the caption. Moving a link into Recipes yourself triggers the same
   search; the app shows *Getting the recipe…*, or *No recipe found* with **Try again**.
5. When you move links from the same website to the same category three times, a domain
   rule is created automatically. Recent corrections are also given to Claude as examples.
6. The app keeps a SQLite copy for offline browsing and search, queues links saved while
   offline, and syncs with `GET /bookmarks?since=…`.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/register`, `/auth/login` | Get a token + API key |
| GET | `/me`, POST `/me/rotate-key` | Account info, new API key |
| POST | `/bookmarks` | `{url, note?}` save (accepts shared text containing a link) |
| GET | `/bookmarks?category=&q=&since=&platform=&favorite=&review=&archived=` | List / search / sync |
| GET/PATCH/DELETE | `/bookmarks/{id}` | Read, edit (category, title, note, favorite, archive), soft delete |
| POST | `/bookmarks/{id}/refresh` | Re-read the link and re-categorize |
| GET/POST/PATCH/DELETE | `/categories` | Manage categories (deleting moves links to Other) |
| GET/POST/DELETE | `/rules` | Domain / keyword rules |
| GET | `/export?format=json\|csv\|html` | Export (html = browser bookmarks file) |
| DELETE | `/me/bookmarks` | Delete all bookmarks |
| GET | `/health` | Health check |

Authenticate with `Authorization: Bearer <token>` (app) or `X-API-Key: <key>` (Shortcut, extension, tray).
