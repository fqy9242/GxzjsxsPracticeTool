"use strict";

const byId = (id) => document.getElementById(id);
const accountRows = byId("accounts");
const runRows = byId("runs");
const notice = byId("notice");
const dialog = byId("account-dialog");
const form = byId("account-form");
const fields = form.elements;
const formError = byId("form-error");
const deleteButton = byId("delete-account");
const saveButton = byId("save-account");
const previewLocationButton = byId("preview-location");
const useLocationButton = byId("use-location");
const locationPreviewResult = byId("location-preview-result");
const mailDialog = byId("mail-dialog");
const mailForm = byId("mail-form");
const mailFields = mailForm.elements;
const mailError = byId("mail-error");
const mailSettingsButton = byId("mail-settings");
const saveMailButton = byId("save-mail");
const navLinks = document.querySelectorAll("[data-view]");
const views = {
  accounts: ["账号管理", "管理实习账号和每日自动签到计划。"],
  history: ["调度中心", "查看最近 100 条执行结果 · 北京时间"],
};
const busyAccounts = new Set();
const results = {
  running: ["执行中", "active"],
  signed: ["签到成功", "positive"],
  already_signed: ["已签到", "positive"],
  unsigned: ["未签到", "muted"],
  failed: ["执行失败", "warning"],
  interrupted: ["已中断", "warning"],
};
const mailResults = {
  sent: "提醒邮件已发送",
  failed: "提醒邮件发送失败",
  not_configured: "未配置发件邮箱",
};
let data = { accounts: [], runs: [], today: "" };
let activeView = "accounts";
let editing = null;
let refreshing = null;
let snapshot = "";
let currentLocation = null;
let locationPreviewRequest = null;

function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  node.className = className;
  return node;
}

function badge(text, tone) {
  const node = element("span", text, "pill");
  node.dataset.tone = tone;
  return node;
}

function notify(text, tone = "active") {
  notice.textContent = text;
  notice.dataset.tone = tone;
  notice.hidden = !text;
}

async function request(path, payload) {
  const options = payload === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("无法连接 PracticeTool 服务。");
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "服务未能完成操作。");
  return result;
}

function timeText(value) {
  return value ? value.slice(5, 16).replace("T", " ") : "—";
}

function actionButton(label, disabled, onClick) {
  const button = element("button", label, "text-button");
  button.type = "button";
  button.disabled = disabled;
  button.addEventListener("click", onClick);
  return button;
}

function accountRow(account) {
  const row = document.createElement("tr");
  const identity = element("td");
  identity.append(element("strong", account.name, "account-name"), element("span", account.username, "account-sub"));
  if (account.reminder_enabled) identity.append(element("span", "邮件提醒已开启", "account-sub"));
  const plan = element("td");
  if (account.enabled) {
    plan.append(element("strong", account.check_time), element("span", account.weekdays_only ? "周一至周五" : "每天", "account-sub"));
  } else {
    plan.append(badge("已关闭", "muted"));
  }
  const session = element("td");
  session.append(badge(account.has_session ? "已缓存" : "待建立", account.has_session ? "positive" : "muted"));
  const todayResult = element("td");
  let result = account.started_at?.startsWith(data.today) ? results[account.state] : null;
  if (account.signed_today && account.state !== "running") result = results.already_signed;
  todayResult.append(badge(...(result || ["待核对", "muted"])));
  if (result && account.message) todayResult.title = account.message;
  const actions = element("td", "", "action-cell");
  const busy = account.state === "running" || busyAccounts.has(account.id);
  actions.append(
    actionButton("查询", busy, () => runAccount(account.id, "status")),
    actionButton("签到", busy, () => runAccount(account.id, "manual")),
    actionButton("编辑", busy, () => openEditor(account)),
  );
  row.append(identity, plan, session, todayResult, actions);
  return row;
}

function runRow(run) {
  const row = document.createElement("tr");
  const state = element("td");
  state.append(badge(...(results[run.state] || ["待核对", "muted"])));
  const source = { manual: "手动签到", automatic: "自动签到", status: "查询状态" };
  const receipt = element("td", run.message || "正在等待平台结果…", "result-cell");
  if (mailResults[run.mail_state]) receipt.append(element("span", mailResults[run.mail_state], "account-sub"));
  row.append(
    element("td", timeText(run.started_at), "time-cell"),
    element("td", run.name),
    element("td", source[run.source] || "—"),
    state,
    receipt,
  );
  return row;
}

function renderAccounts() {
  byId("total-count").textContent = data.accounts.length;
  byId("scheduled-count").textContent = data.accounts.filter((account) => account.enabled).length;
  byId("signed-count").textContent = data.accounts.filter((account) => account.signed_today).length;
  byId("account-empty").hidden = data.accounts.length > 0;
  accountRows.replaceChildren(...data.accounts.map(accountRow));
}

function refresh() {
  if (!refreshing) {
    refreshing = request("/api/dashboard").then((next) => {
      const key = JSON.stringify(next);
      data = next;
      const service = byId("service");
      service.textContent = data.scheduler ? "调度运行中" : "调度已停止";
      service.dataset.tone = data.scheduler ? "positive" : "warning";
      if (key !== snapshot) {
        snapshot = key;
        renderAccounts();
        byId("history-empty").hidden = data.runs.length > 0;
        runRows.replaceChildren(...data.runs.map(runRow));
      }
    }).catch(() => {
      byId("service").textContent = "服务未连接";
      byId("service").dataset.tone = "warning";
    }).finally(() => { refreshing = null; });
  }
  return refreshing;
}

function syncView() {
  const link = [...navLinks].find((item) => item.pathname === location.pathname);
  activeView = link?.dataset.view || "accounts";
  const [title, description] = views[activeView];
  document.title = `${title} · PracticeTool`;
  byId("page-title").textContent = title;
  byId("page-description").textContent = description;
  byId("account-actions").hidden = activeView !== "accounts";
  if (activeView !== "accounts") {
    dialog.close();
    mailDialog.close();
  }
  for (const item of navLinks) {
    const selected = item.dataset.view === activeView;
    byId(`${item.dataset.view}-view`).hidden = !selected;
    if (selected) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  }
  refresh();
}

function updateLocationFields() {
  const showSite = fields.location_mode.value === "site";
  byId("location-fields").hidden = !showSite;
  fields.site_latitude.disabled = fields.site_longitude.disabled = fields.site_radius.disabled = !showSite;
}

function resetLocationPreview() {
  currentLocation = null;
  locationPreviewRequest = null;
  previewLocationButton.disabled = false;
  useLocationButton.hidden = true;
  locationPreviewResult.hidden = true;
}

function distanceMetres(fromLat, fromLng, toLat, toLng) {
  const toRadians = Math.PI / 180;
  const firstLatitude = fromLat * toRadians;
  const secondLatitude = toLat * toRadians;
  const deltaLatitude = (toLat - fromLat) * toRadians;
  const deltaLongitude = (toLng - fromLng) * toRadians;
  const chord = Math.sin(deltaLatitude / 2) ** 2
    + Math.cos(firstLatitude) * Math.cos(secondLatitude) * Math.sin(deltaLongitude / 2) ** 2;
  const earthRadiusMetres = 6378137;
  return Math.round(2 * earthRadiusMetres * Math.asin(Math.sqrt(Math.min(1, chord))) * 1e4) / 1e4;
}

function renderLocationPreview() {
  if (!currentLocation) return;
  const { latitude, longitude, accuracy } = currentLocation;
  const details = [`纬度 ${latitude.toFixed(6)} · 经度 ${longitude.toFixed(6)}`];
  details.push(accuracy === null ? "Windows 未报告定位精度。" : `定位误差半径约 ${Math.ceil(accuracy)} 米。`);
  const siteLat = fields.site_latitude.valueAsNumber;
  const siteLng = fields.site_longitude.valueAsNumber;
  const radius = fields.site_radius.valueAsNumber;
  if (fields.site_latitude.validity.valid && fields.site_longitude.validity.valid
      && Number.isFinite(siteLat) && Number.isFinite(siteLng) && radius > 0) {
    const distance = distanceMetres(latitude, longitude, siteLat, siteLng);
    const status = distance <= radius ? "在范围内" : "超出范围";
    details.push(`距参考点约 ${distance.toFixed(1)} 米，${status}（${radius} 米）。`);
    if (accuracy !== null && accuracy > radius) details.push("定位误差半径超过设定范围，结果可能波动。");
  }
  locationPreviewResult.textContent = details.join(" ");
  locationPreviewResult.hidden = false;
  useLocationButton.hidden = false;
}

function updateReminderFields() {
  const enabled = fields.reminder_enabled.checked;
  byId("reminder-fields").hidden = !enabled;
  fields.reminder_email.disabled = !enabled;
}

function openEditor(account = null) {
  editing = account;
  form.reset();
  resetLocationPreview();
  formError.hidden = true;
  byId("dialog-title").textContent = account ? "编辑账号" : "添加账号";
  fields.name.value = account?.name || "";
  fields.username.value = account?.username || "";
  fields.username.readOnly = Boolean(account);
  fields.password.placeholder = account ? "留空保留原密码" : "";
  fields.password.required = !account;
  const site = account?.check_location;
  fields.location_mode.value = site ? "site" : "current";
  fields.site_latitude.value = site?.latitude ?? "";
  fields.site_longitude.value = site?.longitude ?? "";
  fields.site_radius.value = site?.radius ?? 100;
  updateLocationFields();
  fields.enabled.checked = Boolean(account?.enabled);
  fields.check_time.value = account?.check_time || "09:00";
  fields.weekdays_only.value = account?.weekdays_only === 0 ? "false" : "true";
  fields.reminder_enabled.checked = Boolean(account?.reminder_enabled);
  fields.reminder_email.value = account?.reminder_email || "";
  updateReminderFields();
  deleteButton.hidden = !account;
  dialog.showModal();
  fields.name.focus();
}

function showFormError(error) {
  formError.textContent = error.message || "操作失败。";
  formError.hidden = false;
}

async function runAccount(id, action) {
  if (busyAccounts.has(id)) return;
  busyAccounts.add(id);
  renderAccounts();
  try {
    await request("/api/accounts/run", { id, action });
    notify(action === "status" ? "已发起状态查询，可在调度中心查看结果。" : "已发起签到，可在调度中心查看回执。");
    await refresh();
  } catch (error) {
    notify(error.message, "warning");
  } finally {
    busyAccounts.delete(id);
    renderAccounts();
  }
}

for (const link of navLinks) {
  link.addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (location.pathname !== link.pathname) history.pushState(null, "", link.pathname);
    syncView();
  });
}
window.addEventListener("popstate", syncView);
byId("add-account").addEventListener("click", () => openEditor());
byId("close-dialog").addEventListener("click", () => dialog.close());
byId("cancel-dialog").addEventListener("click", () => dialog.close());
fields.location_mode.addEventListener("change", updateLocationFields);
fields.reminder_enabled.addEventListener("change", updateReminderFields);
dialog.addEventListener("close", () => {
  fields.password.value = "";
  resetLocationPreview();
});

previewLocationButton.addEventListener("click", async () => {
  resetLocationPreview();
  previewLocationButton.disabled = true;
  locationPreviewResult.textContent = "正在读取 Windows 定位…";
  locationPreviewResult.hidden = false;
  const pending = request("/api/accounts/location-preview", {});
  locationPreviewRequest = pending;
  try {
    const result = await pending;
    if (locationPreviewRequest !== pending) return;
    currentLocation = result;
    renderLocationPreview();
  } catch (error) {
    if (locationPreviewRequest === pending) locationPreviewResult.textContent = error.message;
  } finally {
    if (locationPreviewRequest === pending) {
      locationPreviewRequest = null;
      previewLocationButton.disabled = false;
    }
  }
});

useLocationButton.addEventListener("click", () => {
  if (!currentLocation) return;
  fields.site_latitude.value = currentLocation.latitude;
  fields.site_longitude.value = currentLocation.longitude;
  renderLocationPreview();
});

for (const field of [fields.site_latitude, fields.site_longitude, fields.site_radius]) {
  field.addEventListener("input", renderLocationPreview);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (saveButton.disabled) return;
  saveButton.disabled = deleteButton.disabled = true;
  formError.hidden = true;
  try {
    await request("/api/accounts", {
      id: editing?.id ?? null,
      name: fields.name.value,
      username: fields.username.value,
      password: fields.password.value,
      check_location: fields.location_mode.value === "site" ? {
        latitude: fields.site_latitude.valueAsNumber,
        longitude: fields.site_longitude.valueAsNumber,
        radius: fields.site_radius.valueAsNumber,
      } : null,
      enabled: fields.enabled.checked,
      check_time: fields.check_time.value,
      weekdays_only: fields.weekdays_only.value === "true",
      reminder_enabled: fields.reminder_enabled.checked,
      reminder_email: fields.reminder_enabled.checked ? fields.reminder_email.value : editing?.reminder_email || "",
    });
    dialog.close();
    notify("账号已保存。");
    await refresh();
  } catch (error) {
    showFormError(error);
  } finally {
    saveButton.disabled = deleteButton.disabled = false;
  }
});

deleteButton.addEventListener("click", async () => {
  if (!editing || !window.confirm("删除此账号及其本机签到记录？")) return;
  deleteButton.disabled = saveButton.disabled = true;
  try {
    await request("/api/accounts/delete", { id: editing.id });
    dialog.close();
    notify("账号已删除。");
    await refresh();
  } catch (error) {
    showFormError(error);
  } finally {
    deleteButton.disabled = saveButton.disabled = false;
  }
});

mailSettingsButton.addEventListener("click", async () => {
  mailSettingsButton.disabled = true;
  try {
    const config = await request("/api/mail-settings");
    if (activeView !== "accounts") return;
    mailForm.reset();
    mailError.hidden = true;
    mailFields.sender.value = config.sender;
    mailFields.host.value = config.host;
    mailFields.port.value = config.port;
    mailFields.security.value = config.security;
    mailFields.password.required = !config.configured;
    mailFields.password.placeholder = config.configured ? "留空保留已保存的凭据" : "";
    mailDialog.showModal();
  } catch (error) {
    notify(error.message, "warning");
  } finally {
    mailSettingsButton.disabled = false;
  }
});

byId("close-mail").addEventListener("click", () => mailDialog.close());
byId("cancel-mail").addEventListener("click", () => mailDialog.close());
mailDialog.addEventListener("close", () => { mailFields.password.value = ""; });
mailFields.security.addEventListener("change", () => {
  const ssl = mailFields.security.value === "ssl";
  if (mailFields.port.valueAsNumber === (ssl ? 587 : 465)) mailFields.port.value = ssl ? 465 : 587;
});

mailForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (saveMailButton.disabled) return;
  saveMailButton.disabled = true;
  mailError.hidden = true;
  try {
    await request("/api/mail-settings", {
      sender: mailFields.sender.value,
      host: mailFields.host.value,
      port: mailFields.port.valueAsNumber,
      security: mailFields.security.value,
      password: mailFields.password.value,
    });
    mailDialog.close();
    notify("发件设置已保存。");
  } catch (error) {
    mailError.textContent = error.message;
    mailError.hidden = false;
  } finally {
    saveMailButton.disabled = false;
  }
});

syncView();
setInterval(() => { if (!document.hidden) refresh(); }, 4000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
