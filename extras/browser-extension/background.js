// Toolbar button / Ctrl+Shift+S saves the current tab; right-click saves a link.

async function save(url) {
  const { server, apiKey } = await chrome.storage.sync.get(["server", "apiKey"]);
  if (!server || !apiKey) {
    chrome.runtime.openOptionsPage();
    return;
  }
  let message = "Saved ✓";
  try {
    const r = await fetch(server.replace(/\/+$/, "") + "/bookmarks", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
      body: JSON.stringify({ url }),
    });
    if (!r.ok) message = `Couldn't save (${r.status})`;
  } catch (e) {
    message = "Couldn't reach the LinkVault server";
  }
  chrome.notifications.create({
    type: "basic", iconUrl: "icon.png", title: "LinkVault", message: `${message}\n${url}`,
  });
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "save-link", title: "Save link to LinkVault", contexts: ["link"] });
  chrome.contextMenus.create({ id: "save-page", title: "Save page to LinkVault", contexts: ["page"] });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  save(info.menuItemId === "save-link" ? info.linkUrl : tab.url);
});

chrome.action.onClicked.addListener((tab) => save(tab.url));
