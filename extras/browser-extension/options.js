const $ = (id) => document.getElementById(id);

chrome.storage.sync.get(["server", "apiKey"]).then(({ server, apiKey }) => {
  $("server").value = server || "";
  $("apiKey").value = apiKey || "";
});

$("save").addEventListener("click", async () => {
  await chrome.storage.sync.set({ server: $("server").value.trim(), apiKey: $("apiKey").value.trim() });
  $("status").textContent = "Saved";
});
