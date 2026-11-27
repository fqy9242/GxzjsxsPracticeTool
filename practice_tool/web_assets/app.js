"use strict";

const locationButton = document.querySelector("#locate");
const locationResult = document.querySelector("#location-result");
const statusForm = document.querySelector("#status-form");
const statusButton = document.querySelector("#query-status");
const checkInButton = document.querySelector("#check-in");
const statusResult = document.querySelector("#status-result");

function showResult(element, kind, message) {
  element.hidden = false;
  element.dataset.kind = kind;
  element.textContent = message;
}

async function postJson(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("无法连接 PracticeTool，请检查服务是否正在运行。");
  }

  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error("PracticeTool 返回了无法识别的响应。");
  }
  if (!response.ok) {
    throw new Error(result.error || "请求失败，请稍后重试。");
  }
  return result;
}

function currentPosition() {
  if (!window.isSecureContext || !navigator.geolocation) {
    return Promise.reject(new Error("请使用支持定位的浏览器，通过 HTTPS 或本机地址打开此页面。"));
  }
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: true,
      maximumAge: 0,
      timeout: 15000,
    });
  });
}

const locationErrors = {
  1: "请在浏览器的站点设置中允许访问位置，然后重试。",
  2: "设备暂时无法定位，请检查系统定位设置后重试。",
  3: "获取位置超时，请在定位信号较好的地方重试。",
};

locationButton.addEventListener("click", async () => {
  locationButton.disabled = true;
  showResult(locationResult, "waiting", "正在等待设备定位…");
  try {
    const { coords } = await currentPosition();
    showResult(locationResult, "waiting", "正在检查定位结果…");
    const result = await postJson("/api/location", {
      latitude: coords.latitude,
      longitude: coords.longitude,
      accuracy: coords.accuracy,
    });
    showResult(locationResult, "success", `PracticeTool 已收到位置，精度约 ${Math.ceil(result.accuracy)} 米。`);
  } catch (error) {
    showResult(locationResult, "error", locationErrors[error.code] || error.message || "无法获取位置。");
  } finally {
    locationButton.disabled = false;
  }
});

statusForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (statusButton.disabled) return;
  const submitCheckIn = event.submitter === checkInButton;
  statusButton.disabled = checkInButton.disabled = true;
  const data = new FormData(statusForm);
  const credentials = { username: data.get("username"), password: data.get("password") };
  try {
    if (submitCheckIn) {
      showResult(statusResult, "waiting", "正在获取当前位置…");
      const { coords } = await currentPosition();
      showResult(statusResult, "waiting", "正在通过平台接口签到…");
      const result = await postJson("/api/check-in", {
        ...credentials, latitude: coords.latitude, longitude: coords.longitude,
      });
      showResult(statusResult, "success", result.alreadySigned ? "平台显示今天已经签到。" : "签到成功。");
    } else {
      showResult(statusResult, "waiting", "正在登录并查询…");
      const result = await postJson("/api/status", credentials);
      showResult(statusResult, result.signedToday ? "success" : "info",
        result.signedToday ? "今天已经签到。" : "今天尚未签到。");
    }
  } catch (error) {
    showResult(statusResult, "error", locationErrors[error.code] || error.message || "请求失败。");
  } finally {
    statusForm.elements.password.value = "";
    statusButton.disabled = checkInButton.disabled = false;
  }
});
