const DEFAULT_RECEIVER_HOST = "127.0.0.1";
const DEFAULT_RECEIVER_PORT = 17890;

function parseUrl(url) {
  try {
    return new URL(url);
  } catch {
    return null;
  }
}

function normalizeHost(host) {
  return String(host || "").trim().toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
}

function isLocalHost(host) {
  const value = normalizeHost(host);
  return (
    value === "localhost" ||
    value === "0.0.0.0" ||
    value === "::1" ||
    value.startsWith("127.")
  );
}

function getHttpHostname(url) {
  const parsed = parseUrl(url || "");
  if (!parsed) return "";
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
  return normalizeHost(parsed.hostname);
}

function getPageContextHost(details) {
  return (
    getHttpHostname(details.initiator) ||
    getHttpHostname(details.documentUrl) ||
    getHttpHostname(details.originUrl)
  );
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

async function getTabHost(tabId) {
  if (tabId < 0) return "";

  try {
    const tab = await chrome.tabs.get(tabId);
    return getHttpHostname(tab.url || "");
  } catch {
    return "";
  }
}

async function postReport(tabHost, requestHost) {
  tabHost = normalizeHost(tabHost);
  requestHost = normalizeHost(requestHost);

  if (!tabHost || !requestHost) return;
  if (isLocalHost(tabHost) || isLocalHost(requestHost)) return;

  const config = await getReceiverConfig();
  await fetch(`http://${config.host}:${config.port}/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tabHost, requestHost })
  });
}

async function reportRequest(details) {
  const requestHost = getHttpHostname(details.url);
  if (!requestHost || isLocalHost(requestHost)) return;

  let tabHost = getPageContextHost(details);
  if (!tabHost) {
    tabHost = await getTabHost(details.tabId);
  }

  if (!tabHost || isLocalHost(tabHost)) return;

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
