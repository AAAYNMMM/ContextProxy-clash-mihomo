const DEFAULT_RECEIVER_HOST = "127.0.0.1";
const DEFAULT_RECEIVER_PORT = 17890;

const tabHostCache = new Map();

console.log("ContextProxy reporter loaded");

function getHostname(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
}

function normalizeHost(host) {
  return String(host || "").trim().toLowerCase();
}

async function getReceiverConfig() {
  const data = await chrome.storage.local.get({
    receiverHost: DEFAULT_RECEIVER_HOST,
    receiverPort: DEFAULT_RECEIVER_PORT
  });

  const port = Number(data.receiverPort);
  return {
    host: data.receiverHost || DEFAULT_RECEIVER_HOST,
    port: Number.isInteger(port) && port >= 1 && port <= 65535 ? port : DEFAULT_RECEIVER_PORT
  };
}

async function postReport(tabHost, requestHost) {
  tabHost = normalizeHost(tabHost);
  requestHost = normalizeHost(requestHost);
  if (!tabHost || !requestHost) return;

  const config = await getReceiverConfig();
  await fetch(`http://${config.host}:${config.port}/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tabHost, requestHost })
  });
}

async function getTabHost(tabId) {
  if (tabId < 0) return "";

  const cached = tabHostCache.get(tabId);
  if (cached) return cached;

  try {
    const tab = await chrome.tabs.get(tabId);
    const host = getHostname(tab.url || "");
    if (host) tabHostCache.set(tabId, host);
    return host;
  } catch {
    return "";
  }
}

function rememberTabHost(tabId, url) {
  if (tabId < 0) return "";
  const host = getHostname(url || "");
  if (host) tabHostCache.set(tabId, host);
  return host;
}

function getRequestContextHost(details) {
  return (
    getHostname(details.initiator || "") ||
    getHostname(details.documentUrl || "") ||
    getHostname(details.originUrl || "")
  );
}

async function reportRequest(details) {
  const requestHost = getHostname(details.url);
  if (!requestHost) return;

  let tabHost = getRequestContextHost(details);
  if (tabHost && details.tabId >= 0) {
    tabHostCache.set(details.tabId, tabHost);
  }

  if (!tabHost && details.tabId >= 0) {
    tabHost = tabHostCache.get(details.tabId) || "";
  }

  if (!tabHost) {
    tabHost = await getTabHost(details.tabId);
  }

  if (!tabHost) return;

  try {
    await postReport(tabHost, requestHost);
  } catch {
    // The local core may be stopped. Ignore failures; future real requests will
    // report again when the core is available. Do not queue, poll, or send
    // synthetic reports.
  }
}

chrome.webRequest.onBeforeRequest.addListener(
  reportRequest,
  { urls: ["<all_urls>"] }
);

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.url || tab.url) {
    rememberTabHost(tabId, changeInfo.url || tab.url || "");
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  tabHostCache.delete(tabId);
});
