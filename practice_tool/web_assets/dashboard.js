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
const busyAccounts = new Set();
const results = {
  running: ["执行中", "active"],
  signed: ["签到成功", "positive"],
  already_signed: ["已签到", "positive"],
  unsigned: ["未签到", "muted"],
  failed: ["执行失败", "warning"],
  interrupted: ["已中断", "warning"],
};
let data = { accounts: [], runs: [], today: "" };
let editing = null;
let refreshing = null;
let snapshot = "";

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
  row.append(
    element("td", timeText(run.started_at), "time-cell"),
    element("td", run.name),
    element("td", source[run.source] || "—"),
    state,
    element("td", run.message || "正在等待平台结果…", "result-cell"),
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

function setView(view) {
  const history = view === "history";
  byId("accounts-view").hidden = history;
  byId("history-view").hidden = !history;
  byId("add-account").hidden = history;
  byId("page-title").textContent = history ? "调度中心" : "账号管理";
  byId("page-description").textContent = history ? "查看最近 100 条执行结果 · 北京时间" : "管理实习账号和每日自动签到计划。";
  for (const button of document.querySelectorAll("[data-view]")) {
    if (button.dataset.view === view) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
}

function updateLocationFields() {
  const showSite = fields.location_mode.value === "site";
  byId("location-fields").hidden = !showSite;
  fields.site_latitude.disabled = fields.site_longitude.disabled = fields.site_radius.disabled = !showSite;
}

function openEditor(account = null) {
  editing = account;
  form.reset();
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

for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => { setView(button.dataset.view); refresh(); });
}
byId("add-account").addEventListener("click", () => openEditor());
byId("close-dialog").addEventListener("click", () => dialog.close());
byId("cancel-dialog").addEventListener("click", () => dialog.close());
fields.location_mode.addEventListener("change", updateLocationFields);
dialog.addEventListener("close", () => { fields.password.value = ""; });

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

refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 4000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
