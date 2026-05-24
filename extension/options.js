const DEFAULT_RECEIVER_HOST = "127.0.0.1";
const DEFAULT_RECEIVER_PORT = 17890;

const hostInput = document.getElementById("receiverHost");
const portInput = document.getElementById("receiverPort");
const statusEl = document.getElementById("status");
const saveButton = document.getElementById("saveButton");
const resetButton = document.getElementById("resetButton");
const testButton = document.getElementById("testButton");

function setStatus(message, type = "") {
  statusEl.textContent = message;
  statusEl.className = type;
}

function readFormConfig() {
  const host = hostInput.value.trim() || DEFAULT_RECEIVER_HOST;
  const port = Number(portInput.value);

  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("端口必须是 1-65535 的数字");
  }

  return {
    receiverHost: host,
    receiverPort: port
  };
}

async function loadOptions() {
  const data = await chrome.storage.local.get({
    receiverHost: DEFAULT_RECEIVER_HOST,
    receiverPort: DEFAULT_RECEIVER_PORT
  });

  hostInput.value = data.receiverHost || DEFAULT_RECEIVER_HOST;
  portInput.value = Number(data.receiverPort) || DEFAULT_RECEIVER_PORT;
}

async function saveOptions() {
  try {
    const config = readFormConfig();
    await chrome.storage.local.set(config);
    setStatus("设置已保存", "success");
  } catch (err) {
    setStatus(err.message || "保存失败", "error");
  }
}

async function resetOptions() {
  hostInput.value = DEFAULT_RECEIVER_HOST;
  portInput.value = DEFAULT_RECEIVER_PORT;
  await chrome.storage.local.set({
    receiverHost: DEFAULT_RECEIVER_HOST,
    receiverPort: DEFAULT_RECEIVER_PORT
  });
  setStatus("已恢复默认设置", "success");
}

async function testConnection() {
  let config;
  try {
    config = readFormConfig();
  } catch (err) {
    setStatus(err.message || "端口设置无效", "error");
    return;
  }

  const baseUrl = `http://${config.receiverHost}:${config.receiverPort}`;

  try {
    const healthResponse = await fetch(`${baseUrl}/health`, { method: "GET" });
    if (healthResponse.ok) {
      setStatus("连接成功", "success");
      return;
    }
  } catch {
    // Fall back to /report below.
  }

  try {
    const reportResponse = await fetch(`${baseUrl}/report`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        tabHost: "extension-test.local",
        requestHost: "extension-test.local"
      })
    });

    if (reportResponse.ok) {
      setStatus("连接成功", "success");
      return;
    }
  } catch {
    // Show a user-facing failure below.
  }

  setStatus("连接失败，请检查客户端是否启动以及端口是否一致", "error");
}

saveButton.addEventListener("click", saveOptions);
resetButton.addEventListener("click", resetOptions);
testButton.addEventListener("click", testConnection);

loadOptions();
