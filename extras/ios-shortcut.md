# iPhone sharing: Apple Shortcut

Works today, no Mac or Xcode needed. It adds **Save to LinkVault** to the share sheet
of every app and sends the link to your server.

You need: your server address (e.g. `https://vault.example.com` or a `trycloudflare.com`
tunnel address) and your API key (LinkVault app > Settings > API key > copy). The key
starts with `lv_`.

## Build it (3 minutes)

1. Open **Shortcuts** > **+**. Rename it `Save to LinkVault`.
2. Tap **ⓘ** and turn on **Show in Share Sheet**.
3. On the top block *"Receive **Any** input from **Share Sheet**"*: tap **Any** and keep only
   **URLs** and **Text**. Set *"If there's no input"* to **Get Clipboard**.
4. Add **Get Contents of URL**. Tap its blue **URL** and enter, in this order:
   - `https://YOUR-SERVER/s/`
   - your API key, then `/`
   - the **Shortcut Input** variable (tap it in the bar above the keyboard)

   It reads: `https://YOUR-SERVER/s/lv_xxxxxxxx/ [Shortcut Input]`

   Leave **Show More** alone: no method, headers or body are needed.
5. Add **Show Notification** and make its text the **Contents of URL** variable.
   The server answers `Saved ✓ <title>`.

Paste the shared link raw: the server rebuilds it, including its `?query`.
(`GET /save?key=...&url=<URL-encoded link>` also works, if you prefer to add a
**URL Encode** action.)

## Use it

In Instagram, TikTok, YouTube, X, Safari… tap **Share** > **Save to LinkVault**.
In Instagram: *Share* > *More* (•••). The first time, tap *Edit Actions* and add the
shortcut to Favourites so it's always at the top.

The first run asks to allow the connection to your server: tap **Always Allow**.

## If your server address changes

A quick Cloudflare tunnel (`trycloudflare.com`) gets a new address every time it starts.
Edit the shortcut and replace the part before `/s/`. A named tunnel on your own domain
keeps the same address.

## Keep the key private

The key gives full access to your account (it works for the whole API, not just saving).
If it leaks: LinkVault > Settings, or `POST /me/rotate-key`, then update
the shortcut. The server hides the key in its logs.
