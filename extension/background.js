const DEFAULT_RECEIVER_HOST = "127.0.0.1";
const DEFAULT_RECEIVER_PORT = 17890;

const HEALTH_CHECK_INTERVAL_MS = 2000;
const OFFLINE_QUEUE_LIMIT = 100;
const REPORT_THROTTLE_MS = 500;

let receiverOnline = false;
let lastHealthCheckAt = 0;
let offlineQueue = [];
let recentReportAt = new Map();
let tabHostCache = new Map();

console.log("ContextProxy extension loaded");

function getHostname(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
}

function makeEventKey(payload) {
  return `${payload.tabHost}|${payload.requestHost}`.toLowerCase();
}

function rememberOffline(payload) {
  if (!payload.tabHost || !payload.requestHost) return;

  const key = makeEventKey(payload);
  offlineQueue = offlineQueue.filter((item) => makeEventKey(item) !== key);
  offlineQueue.push({ ...payload, createdAt: Date.now() });

  if (offlineQueue.length > OFFLINE_QUEUE_LIMIT) {
    offlineQueue = offlineQueue.slice(offlineQueue.length - OFFLINE_QUEUE_LIMIT);
  }
}

function shouldReportNow(payload) {
  const key = makeEventKey(payload);
  const now = Date.now();
  const last = recentReportAt.get(key) || 0;

  if (now - last < REPORT_THROTTLE_MS) {
    return false;
  }

  recentReportAt.set(key, now);
  return true;
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
  if (host) {
    tabHostCache.set(tabId, host);
  }
  return host;
}

function getRequestContextHost(details) {
  return (
    getHostname(details.initiator || "") ||
    getHostname(details.documentUrl || "") ||
    getHostname(details.originUrl || "")
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

async function getBaseUrl() {
  const config = await getReceiverConfig();
  return `http://${config.host}:${config.port}`;
}

async function getReportUrl() {
  return `${await getBaseUrl()}/report`;
}

async function postReport(payload) {
  const config = await getReceiverConfig();
  const reportUrl = `http://${config.host}:${config.port}/report`;
  const headers = { "Content-Type": "application/json" };
  const response = await fetch(reportUrl, {
    method: "POST",
    headers,
    body: JSON.stringify({
      tabHost: payload.tabHost,
      requestHost: payload.requestHost
    })
  });

  if (!response.ok) {
    throw new Error(`report failed: ${response.status}`);
  }
}

async function registerBrowser() {
  try {
    const config = await getReceiverConfig();
    const headers = { "Content-Type": "application/json" };
    await fetch(`http://${config.host}:${config.port}/register_browser`, {
      method: "POST",
      headers,
      body: "{}"
    });
  } catch (err) {
    console.warn("[ContextProxy] register browser failed:", err);
  }
}

async function reportPayload(payload, options = {}) {
  if (!payload.tabHost || !payload.requestHost) return;

  if (!options.force && !shouldReportNow(payload)) return;

  try {
    await postReport(payload);
    receiverOnline = true;
  } catch (err) {
    receiverOnline = false;
    rememberOffline(payload);
    console.warn("[ContextProxy] report failed:", err);
  }
}

async function reportRequest(details) {
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
  const requestHost = getHostname(details.url);

  if (!tabHost || !requestHost) return;

  await reportPayload({ tabHost, requestHost });
}

async function checkReceiverOnline() {
  const now = Date.now();
  if (now - lastHealthCheckAt < 500) return receiverOnline;
  lastHealthCheckAt = now;

  try {
    const baseUrl = await getBaseUrl();
    const response = await fetch(`${baseUrl}/health`, {
      method: "GET",
      cache: "no-store"
    });

    const wasOnline = receiverOnline;
    receiverOnline = response.ok;

    if (!wasOnline && receiverOnline) {
      await registerBrowser();
      await flushOfflineReports();
      await reportAllOpenTabs();
    }

    return receiverOnline;
  } catch {
    receiverOnline = false;
    return false;
  }
}

async function flushOfflineReports() {
  if (!offlineQueue.length) return;

  const queued = offlineQueue.slice();
  offlineQueue = [];

  for (const payload of queued) {
    try {
      await reportPayload(payload, { force: true });
    } catch {
      rememberOffline(payload);
    }
  }
}

async function reportAllOpenTabs() {
  let tabs = [];

  try {
    tabs = await chrome.tabs.query({});
  } catch {
    return;
  }

  for (const tab of tabs) {
    const tabHost = getHostname(tab.url || "");
    if (!tabHost) continue;

    // 上报当前标签页自身 host，帮助代理后启动时快速恢复 Tab 上下文。
    await reportPayload({ tabHost, requestHost: tabHost }, { force: true });
  }
}

chrome.webRequest.onBeforeRequest.addListener(
  reportRequest,
  { urls: ["<all_urls>"] }
);

chrome.tabs.onActivated.addListener(async (activeInfo) => {
  const tabHost = await getTabHost(activeInfo.tabId);
  if (tabHost) {
    await reportPayload({ tabHost, requestHost: tabHost }, { force: true });
  }
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete" && !changeInfo.url) return;

  const tabHost = rememberTabHost(tabId, tab.url || changeInfo.url || "");
  if (tabHost) {
    await reportPayload({ tabHost, requestHost: tabHost }, { force: true });
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  tabHostCache.delete(tabId);
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("contextproxy-health", { periodInMinutes: 0.5 });
  registerBrowser();
  checkReceiverOnline();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("contextproxy-health", { periodInMinutes: 0.5 });
  registerBrowser();
  checkReceiverOnline();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "contextproxy-health") {
    checkReceiverOnline();
  }
});

setInterval(checkReceiverOnline, HEALTH_CHECK_INTERVAL_MS);
registerBrowser();
checkReceiverOnline();
