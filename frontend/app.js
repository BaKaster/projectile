import {
  analysisReportUrl,
  answerAnalysisQuestions,
  createChat,
  deleteChat,
  downloadAnalysisExcel,
  formatApiError,
  getAnalysisRun,
  getChat,
  getProjectTypes,
  listChats,
  sendChatMessage,
  skipAnalysisQuestions,
  updateAnalysisProjectType,
  updateChat,
  uploadDocuments,
  uploadRateImport,
  applyRateImport,
} from "./api.js";
import { ACTIVE_STATUSES, pollAnalysis, SUCCESS_STATUSES } from "./polling.js";

const apiBase = window.PROJECTILE_CONFIG?.apiBase || "http://localhost:8000";
const state = { chat: null, run: null, files: [], projectTypes: [], busy: false, pollController: null };
const $ = (selector) => document.querySelector(selector);
const elements = {
  header: $(".chat-header"),
  sidebar: $("#sidebar"), overlay: $("#sidebar-overlay"), history: $("#chat-history"),
  newChat: $("#new-chat"), openSidebar: $("#open-sidebar"), closeSidebar: $("#close-sidebar"),
  title: $("#chat-title"), subtitle: $("#chat-subtitle"),
  conversation: $("#conversation"), empty: $("#empty-state"),
  composer: $("#composer"), input: $("#message-input"), send: $("#send-button"),
  attach: $("#attach-button"), fileInput: $("#file-input"), pendingFiles: $("#pending-files"),
  toast: $("#toast"), excelLoading: $("#excel-loading"),
  themeToggle: $("#theme-toggle"), themeLabel: $("#theme-label"), dropOverlay: $("#drop-overlay"),
  ratesButton: $("#rates-button"), ratesModal: $("#rates-modal"), ratesClose: $("#rates-close"), ratesFile: $("#rates-file"), ratesAuto: $("#rates-auto"), ratesUpload: $("#rates-upload"), ratesResults: $("#rates-results"),
};

let dragDepth = 0;

function setTheme(theme) {
  const dark = theme === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  elements.themeToggle.setAttribute("aria-pressed", String(dark));
  elements.themeToggle.setAttribute("aria-label", dark ? "Включить светлую тему" : "Включить тёмную тему");
  elements.themeLabel.textContent = dark ? "Светлая тема" : "Тёмная тема";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", "#121239");
  syncReportLinks();
}

function syncReportLinks() {
  const theme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  for (const link of document.querySelectorAll("[data-report-project][data-report-run]")) {
    link.href = analysisReportUrl(apiBase, link.dataset.reportProject, link.dataset.reportRun, theme);
    link.download = `projectile-analysis-${link.dataset.reportRun}-${theme}.pdf`;
  }
}

function initialTheme() {
  const saved = localStorage.getItem("projectile-theme");
  return saved === "dark" || saved === "light"
    ? saved
    : window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function hasFiles(event) {
  return Array.from(event.dataTransfer?.types || []).includes("Files");
}

function addFiles(files) {
  const existing = new Set(state.files.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
  for (const file of files) {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (!existing.has(key)) { state.files.push(file); existing.add(key); }
  }
  renderPendingFiles();
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.remove("hidden");
  setTimeout(() => elements.toast.classList.add("hidden"), 4000);
}

function setBusy(value) {
  state.busy = value;
  elements.send.disabled = value;
  elements.input.disabled = value;
  elements.attach.disabled = value;
}

function openRates() { elements.ratesModal.classList.remove("hidden"); elements.ratesFile.focus(); }
function closeRates() { elements.ratesModal.classList.add("hidden"); }
function renderRateImport(result) {
  elements.ratesResults.replaceChildren();
  const info = document.createElement("p");
  info.textContent = result.applied_count ? `Автоматически обновлено: ${result.applied_count}` : `Распознано строк: ${result.extracted_items.length}. Выберите и подтвердите изменения.`;
  elements.ratesResults.append(info);
  const list = document.createElement("div"); list.className = "rates-list";
  result.extracted_items.forEach((item, index) => { const row = document.createElement("label"); row.className = "rates-row"; const box = document.createElement("input"); box.type = "checkbox"; box.checked = item.selected; box.dataset.index = String(index); row.append(box, document.createTextNode(`${item.role_name}: ${item.sale_rate.toLocaleString("ru-RU")} ₽/ч, себестоимость ${item.cost_rate.toLocaleString("ru-RU")} ₽/ч`)); list.append(row); });
  elements.ratesResults.append(list);
  if (result.status !== "applied" && result.extracted_items.length) { const apply = document.createElement("button"); apply.type = "button"; apply.className = "rates-upload"; apply.textContent = "Подтвердить выбранные ставки"; apply.addEventListener("click", async () => { const items = result.extracted_items.map((item, index) => ({ ...item, selected: list.querySelector(`[data-index=\"${index}\"]`).checked })); try { renderRateImport(await applyRateImport(apiBase, result.id, items)); showToast("Ставки обновлены. История сохранена."); } catch (error) { showToast(formatApiError(error)); } }); elements.ratesResults.append(apply); }
}

function setExcelLoading(value) {
  elements.excelLoading.classList.toggle("hidden", !value);
}

async function loadProjectTypes() {
  try { state.projectTypes = await getProjectTypes(apiBase); }
  catch (error) { showToast(formatApiError(error)); }
}

function closeSidebar() {
  elements.sidebar.classList.remove("open");
  elements.overlay.classList.remove("open");
}

function resizeInput() {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 160)}px`;
}

function updateScrollProgress() {
  const maximum = elements.conversation.scrollHeight - elements.conversation.clientHeight;
  const progress = maximum > 0
    ? Math.min(100, Math.max(0, elements.conversation.scrollTop / maximum * 100))
    : 0;
  elements.header.style.setProperty("--chat-scroll-progress", `${progress}%`);
}

function messageDate(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

function formatMessageTime(value) {
  return new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(messageDate(value));
}

async function refreshHistory() {
  try {
    const chats = await listChats(apiBase);
    elements.history.replaceChildren();
    if (!chats.length) {
      const empty = document.createElement("p");
      empty.className = "history-empty";
      empty.textContent = "Здесь появятся ваши диалоги и результаты анализа.";
      elements.history.append(empty);
      return;
    }
    for (const chat of chats) {
      const row = document.createElement("div");
      row.className = `history-row${state.chat?.id === chat.id ? " active" : ""}`;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "history-item";
      const title = document.createElement("strong");
      title.textContent = chat.name;
      const preview = document.createElement("small");
      preview.textContent = chat.last_message || statusLabel(chat.latest_status) || "Новый чат";
      button.append(title, preview);
      button.addEventListener("click", () => openChat(chat.id));
      const actions = document.createElement("span"); actions.className = "history-actions";
      const edit = document.createElement("button"); edit.type = "button"; edit.className = "history-action"; edit.setAttribute("aria-label", `Переименовать чат «${chat.name}»`);
      edit.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 16-.8 4.8L8 20l11-11-4-4L4 16ZM13.5 6.5l4 4"/></svg>';
      edit.addEventListener("click", () => beginHistoryEdit(row, chat));
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "history-action delete"; remove.setAttribute("aria-label", `Удалить чат «${chat.name}»`);
      remove.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m-9 0 1 13h10l1-13M10 11v5m4-5v5"/></svg>';
      remove.addEventListener("click", () => removeHistoryChat(chat));
      actions.append(edit, remove); row.append(button, actions); elements.history.append(row);
    }
  } catch (error) {
    elements.history.innerHTML = '<p class="history-empty">Не удалось загрузить историю.</p>';
  }
}

async function refreshCurrentChatIdentity() {
  if (!state.chat) return;
  const chat = await getChat(apiBase, state.chat.id);
  state.chat.name = chat.name;
  elements.title.textContent = chat.name;
}

function beginHistoryEdit(row, chat) {
  const form = document.createElement("form"); form.className = "history-edit-form";
  const input = document.createElement("input"); input.value = chat.name; input.maxLength = 300; input.setAttribute("aria-label", "Новое название чата");
  const save = document.createElement("button"); save.type = "submit"; save.textContent = "✓"; save.setAttribute("aria-label", "Сохранить название");
  const cancel = document.createElement("button"); cancel.type = "button"; cancel.textContent = "×"; cancel.setAttribute("aria-label", "Отменить переименование");
  cancel.addEventListener("click", refreshHistory);
  input.addEventListener("keydown", (event) => { if (event.key === "Escape") refreshHistory(); });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = input.value.trim();
    if (!name) { showToast("Название чата не может быть пустым."); input.focus(); return; }
    input.disabled = true; save.disabled = true; cancel.disabled = true;
    try {
      const updated = await updateChat(apiBase, chat.id, name);
      if (state.chat?.id === chat.id) { state.chat.name = updated.name; elements.title.textContent = updated.name; }
      await refreshHistory();
    } catch (error) { input.disabled = false; save.disabled = false; cancel.disabled = false; showToast(formatApiError(error)); }
  });
  form.append(input, save, cancel); row.replaceChildren(form); input.focus(); input.select();
}

async function removeHistoryChat(chat) {
  if (!window.confirm(`Удалить чат «${chat.name}» и все результаты его анализа?`)) return;
  try {
    await deleteChat(apiBase, chat.id);
    if (state.chat?.id === chat.id) newChat();
    else await refreshHistory();
  } catch (error) { showToast(formatApiError(error)); }
}

function statusLabel(status, currentStep = "") {
  if (status === "analyzing") {
    return ({
      classifying_and_finding_gaps: "Извлекаем факты и определяем тип проекта",
      building_project_scope: "Формируем этапы и состав работ",
      estimating_roles_and_effort: "Рассчитываем роли и трудозатраты",
      finalizing_analysis: "Собираем итоговый результат",
    })[currentStep] || "Анализируем документы";
  }
  return ({ queued: "В очереди", extracting: "Читаем документы", requires_input: "Нужны ответы", ready: "Анализ готов", failed: "Ошибка анализа" })[status] || "";
}

function newChat() {
  state.pollController?.abort();
  state.chat = null; state.run = null; state.files = [];
  elements.title.textContent = "Новый чат";
  elements.subtitle.textContent = "AI-анализ проектной документации";
  elements.conversation.replaceChildren(elements.empty);
  elements.conversation.scrollTop = 0; updateScrollProgress();
  elements.empty.classList.remove("hidden");
  elements.input.placeholder = "Опишите проект или задайте вопрос…";
  renderPendingFiles(); refreshHistory(); closeSidebar(); elements.input.focus();
}

async function openChat(id) {
  if (state.busy) return;
  state.pollController?.abort(); closeSidebar();
  try {
    const chat = await getChat(apiBase, id);
    state.chat = chat; state.run = chat.latest_analysis;
    elements.title.textContent = chat.name;
    elements.subtitle.textContent = "История анализа проекта";
    elements.conversation.replaceChildren();
    for (const message of chat.messages) renderUserMessage(message.content, [], message.created_at);
    if (chat.latest_analysis) {
      if (ACTIVE_STATUSES.has(chat.latest_analysis.status)) {
        renderThinking(chat.latest_analysis);
        beginPolling(chat.latest_analysis.run_id);
      } else if (SUCCESS_STATUSES.has(chat.latest_analysis.status)) renderAnalysis(chat.latest_analysis);
      else renderFailure(chat.latest_analysis.errors);
    }
    updateQuestionMode(); refreshHistory(); scrollToBottom();
  } catch (error) { showToast(formatApiError(error)); }
}

function messageShell(role, label, createdAt = new Date()) {
  const message = document.createElement("article");
  message.className = `message ${role}`;
  const avatar = document.createElement("div"); avatar.className = "avatar"; avatar.textContent = role === "user" ? "Вы" : "P";
  const body = document.createElement("div"); body.className = "message-body";
  const meta = document.createElement("div"); meta.className = "message-meta";
  const author = document.createElement("span"); author.textContent = label;
  const date = messageDate(createdAt);
  const time = document.createElement("time"); time.className = "message-time";
  time.dateTime = date.toISOString(); time.textContent = formatMessageTime(date);
  meta.append(author, time);
  body.append(meta); message.append(avatar, body); elements.conversation.append(message);
  return body;
}

function renderUserMessage(content, files, createdAt = new Date()) {
  elements.empty.classList.add("hidden");
  const body = messageShell("user", "Вы", createdAt);
  const text = document.createElement("div"); text.className = "message-text"; text.textContent = content; body.append(text);
  if (files.length) {
    const container = document.createElement("div"); container.className = "message-files";
    for (const file of files) { const chip = document.createElement("span"); chip.className = "file-chip"; chip.textContent = file.name; container.append(chip); }
    body.append(container);
  }
}

function renderThinking(run) {
  $("#thinking-message")?.remove();
  const body = messageShell("assistant", "Projectile", run.updated_at || run.created_at);
  body.parentElement.id = "thinking-message";
  const row = document.createElement("div"); row.className = "thinking";
  const dots = document.createElement("span"); dots.className = "thinking-dots"; dots.innerHTML = "<i></i><i></i><i></i>";
  const text = document.createElement("span"); text.textContent = statusLabel(run.status, run.current_step) || "Готовим анализ";
  row.append(dots, text); body.append(row); scrollToBottom();
}

function analysisSection(title, content) {
  const section = document.createElement("section"); section.className = "analysis-section";
  const heading = document.createElement("h3"); heading.textContent = title; section.append(heading, content); return section;
}

function textBlock(text) { const p = document.createElement("p"); p.textContent = text; return p; }
function listBlock(items, map = (item) => item) {
  if (!items?.length) return textBlock("Нет существенных пунктов.");
  const list = document.createElement("ul"); list.className = "analysis-list";
  for (const item of items) { const li = document.createElement("li"); li.textContent = map(item); list.append(li); } return list;
}
function gridBlock(items, cardBuilder) {
  if (!items?.length) return textBlock("Нет существенных пунктов.");
  const grid = document.createElement("div"); grid.className = "analysis-grid";
  for (const item of items) grid.append(cardBuilder(item)); return grid;
}
function card(title, text, extraClass = "") {
  const item = document.createElement("div"); item.className = `analysis-card ${extraClass}`;
  const strong = document.createElement("strong"); strong.textContent = title; item.append(strong, document.createTextNode(text)); return item;
}

function uniqueTextItems(items) {
  const seen = new Set();
  return (items || []).filter((item) => {
    const text = String(item || "").trim();
    const key = text.toLocaleLowerCase("ru-RU").replace(/[.!?;:]+$/g, "");
    if (!key || seen.has(key)) return false;
    seen.add(key); return true;
  });
}

function projectTypeSelect(run) {
  const select = document.createElement("select");
  select.className = "analysis-type-select";
  select.setAttribute("aria-label", "Тип проекта");
  const grouped = new Map();
  for (const item of state.projectTypes) {
    if (!grouped.has(item.direction_code)) grouped.set(item.direction_code, []);
    grouped.get(item.direction_code).push(item);
  }
  if (!state.projectTypes.some((item) => item.code === run.result.project_type_code)) {
    const option = document.createElement("option");
    option.value = run.result.project_type_code || "";
    option.textContent = run.result.project_type_code || "Тип не определён";
    select.append(option);
  }
  for (const [direction, types] of grouped) {
    const group = document.createElement("optgroup"); group.label = direction;
    for (const projectType of types) {
      const option = document.createElement("option"); option.value = projectType.code;
      option.textContent = `${projectType.name} (${projectType.code})`;
      group.append(option);
    }
    select.append(group);
  }
  select.value = run.result.project_type_code || "";
  select.addEventListener("change", async () => {
    const previous = run.result.project_type_code || "";
    select.disabled = true;
    try {
      const updated = await updateAnalysisProjectType(apiBase, state.chat.id, run.run_id, select.value);
      state.run = updated; renderAnalysis(updated);
      showToast("Тип проекта изменён, состав работ и трудозатраты пересчитаны.");
    } catch (error) {
      select.value = previous; select.disabled = false; showToast(formatApiError(error));
    }
  });
  return select;
}

function renderAnalysis(run) {
  $("#thinking-message")?.remove(); $("#analysis-message")?.remove();
  const result = run.result;
  if (!result) { renderFailure([{ message: "Backend завершил анализ без результата" }]); return; }
  state.run = run;
  const body = messageShell("assistant", "Projectile", run.updated_at || run.created_at); body.parentElement.id = "analysis-message";
  const root = document.createElement("div"); root.className = "analysis";
  const top = document.createElement("div"); top.className = "analysis-top";
  const overview = document.createElement("div");
  const label = document.createElement("div"); label.className = "analysis-label"; label.textContent = "Название проекта";
  const projectName = document.createElement("div"); projectName.className = "analysis-project-name";
  projectName.textContent = result.project_name || state.chat?.name || "Проект без названия";
  const typeLabel = document.createElement("label"); typeLabel.className = "analysis-type-label"; typeLabel.textContent = "Тип проекта";
  typeLabel.append(projectTypeSelect(run));
  overview.append(label, projectName, typeLabel);
  const confidence = document.createElement("span"); confidence.className = "confidence"; confidence.textContent = `Уверенность: ${{ low: "низкая", medium: "средняя", high: "высокая" }[result.confidence]}`;
  const download = document.createElement("a"); download.className = "download-button";
  download.dataset.reportProject = state.chat.id;
  download.dataset.reportRun = run.run_id;
  download.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m0 0 5-5m-5 5-5-5M5 19h14"/></svg>';
  download.setAttribute("aria-label", "Скачать анализ в PDF");
  download.title = "Скачать анализ в PDF";
  const topActions = document.createElement("div"); topActions.className = "analysis-top-actions";
  topActions.append(confidence, download);
  top.append(overview, topActions); root.append(top);
  const description = document.createElement("div"); description.className = "analysis-description";
  description.append(textBlock(result.summary));
  if (result.rationale && result.rationale.trim() !== result.summary?.trim()) description.append(textBlock(result.rationale));
  root.append(analysisSection("Подробное описание проекта", description));
  root.append(analysisSection("Проблемы и риски", gridBlock(result.issues, (issue) => card(issue.description, issue.impact_on_estimate))));
  root.append(analysisSection("Допущения и предупреждения", listBlock(uniqueTextItems([...(result.assumptions || []), ...(result.warnings || [])]))));
  root.append(analysisSection("Недостающая информация", gridBlock(result.questions, (question) => card(question.question, question.reason, "question"))));
  root.append(renderExcelPanel(run));
  body.append(root);
  syncReportLinks();
  updateQuestionMode(); scrollToBottom(); refreshHistory();
}

async function generateExcel(run, button = null) {
  const label = button?.querySelector("span");
  const defaultLabel = label?.textContent;
  if (button) button.disabled = true;
  if (label) label.textContent = "Формируем и закрепляем…";
  setExcelLoading(true);
  try {
    const artifact = await downloadAnalysisExcel(apiBase, state.chat.id, run.run_id);
    const url = URL.createObjectURL(artifact.blob);
    const link = document.createElement("a");
    link.href = url; link.download = artifact.filename;
    document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    if (label) label.textContent = artifact.attached ? "Excel закреплён за проектом" : defaultLabel;
    showToast(artifact.attached
      ? "Excel скачан и сохранён в контексте проекта для дальнейшего обсуждения."
      : "Excel скачан.");
    if (artifact.attached && label) setTimeout(() => { label.textContent = defaultLabel; }, 3000);
    return true;
  } catch (error) {
    if (label) label.textContent = defaultLabel;
    showToast(formatApiError(error)); return false;
  } finally {
    if (button) button.disabled = false;
    setExcelLoading(false);
  }
}

function renderExcelPanel(run) {
  const panel = document.createElement("section"); panel.className = "excel-panel";
  const button = document.createElement("button"); button.type = "button"; button.className = "excel-button";
  button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h8l4 4v14H7zM15 3v5h4M10 12l4 5m0-5-4 5"/></svg><span>Сформировать Excel файл</span>';
  button.setAttribute("aria-expanded", "false");
  button.addEventListener("click", async () => {
    const questions = run.result?.questions || [];
    const shouldAskQuestions = questions.length && (
      run.status === "requires_input" ||
      (run.status === "ready" && run.current_step === "questions_skipped")
    );
    if (shouldAskQuestions) {
      const existing = panel.querySelector(".excel-questions");
      if (existing) { existing.querySelector("textarea")?.focus(); return; }
      button.setAttribute("aria-expanded", "true");
      panel.append(buildExcelQuestions(run, questions, button));
      panel.querySelector("textarea")?.focus();
      panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return;
    }
    if (run.status !== "ready") {
      showToast("Дождитесь завершения анализа перед формированием Excel.");
      return;
    }
    await generateExcel(run, button);
  });
  panel.append(button);
  return panel;
}

function buildExcelQuestions(run, questions, trigger) {
  const form = document.createElement("form"); form.className = "excel-questions";
  const title = document.createElement("h3"); title.textContent = "Нужна дополнительная информация";
  const copy = document.createElement("p");
  copy.textContent = "Ответьте на известные вам вопросы — заполнять все поля необязательно. После отправки или пропуска система рассчитает работы, роли и трудозатраты.";
  const list = document.createElement("div"); list.className = "excel-question-list";
  questions.forEach((question, index) => {
    const field = document.createElement("div"); field.className = "excel-question";
    const label = document.createElement("label"); label.htmlFor = `excel-answer-${index}`; label.textContent = question.question;
    if (question.reason) { const reason = document.createElement("small"); reason.textContent = question.reason; label.append(reason); }
    const input = document.createElement("textarea"); input.id = `excel-answer-${index}`; input.name = `answer-${index}`; input.placeholder = "Введите ответ, если информация известна…";
    field.append(label, input); list.append(field);
  });
  const actions = document.createElement("div"); actions.className = "excel-question-actions";
  const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "excel-cancel"; cancel.textContent = "Отмена";
  const skip = document.createElement("button"); skip.type = "button"; skip.className = "excel-skip"; skip.textContent = "Пропустить вопросы";
  const submit = document.createElement("button"); submit.type = "submit"; submit.className = "excel-submit"; submit.textContent = "Отправить ответы";
  cancel.addEventListener("click", () => { trigger.setAttribute("aria-expanded", "false"); form.remove(); });
  skip.addEventListener("click", async () => {
    if (state.busy) return;
    setBusy(true); cancel.disabled = true; skip.disabled = true; submit.disabled = true;
    try {
      state.run = await skipAnalysisQuestions(apiBase, state.chat.id, run.run_id);
      renderAnalysis(state.run);
      await generateExcel(state.run);
    } catch (error) {
      cancel.disabled = false; skip.disabled = false; submit.disabled = false;
      showToast(formatApiError(error));
    } finally {
      setBusy(false); updateQuestionMode();
    }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const answers = questions
      .map((question, index) => ({ question: question.question, answer: form.elements[`answer-${index}`].value.trim() }))
      .filter((item) => item.answer);
    if (!answers.length) { showToast("Заполните хотя бы одно поле или нажмите «Пропустить вопросы»."); return; }
    const content = ["Ответы для формирования Excel:", ...answers.map((item, index) => `${index + 1}. ${item.question}\nОтвет: ${item.answer}`)].join("\n\n");
    setBusy(true); submit.disabled = true; cancel.disabled = true; skip.disabled = true;
    try {
      renderUserMessage(content, []);
      const accepted = await answerAnalysisQuestions(apiBase, state.chat.id, run.run_id, content);
      state.run = accepted.analysis; renderAnalysis(accepted.analysis); refreshHistory();
      await generateExcel(accepted.analysis);
    } catch (error) { setBusy(false); submit.disabled = false; cancel.disabled = false; skip.disabled = false; showToast(formatApiError(error)); }
  });
  actions.append(cancel, skip, submit); form.append(title, copy, list, actions);
  return form;
}

function renderFailure(errors = []) {
  $("#thinking-message")?.remove();
  const body = messageShell("assistant", "Projectile");
  const text = document.createElement("div"); text.className = "message-text";
  text.textContent = `Не удалось завершить анализ. ${errors.map((error) => error.message || error.detail || String(error)).join("; ")}`;
  body.append(text); scrollToBottom();
}

function updateQuestionMode() {
  const requires = state.run?.status === "requires_input";
  elements.input.placeholder = requires ? "Ответьте на вопросы для уточнения анализа…" : "Продолжите диалог или добавьте документы…";
}

async function beginPolling(runId) {
  state.pollController?.abort(); state.pollController = new AbortController();
  try {
    const run = await pollAnalysis({
      fetchRun: (signal) => getAnalysisRun(apiBase, state.chat.id, runId, signal),
      onUpdate: (current) => { state.run = current; renderThinking(current); },
      signal: state.pollController.signal,
    });
    if (!run) return;
    state.run = run;
    await refreshCurrentChatIdentity();
    if (SUCCESS_STATUSES.has(run.status)) renderAnalysis(run); else renderFailure(run.errors);
  } catch (error) { if (error.name !== "AbortError") showToast(formatApiError(error)); }
  finally { setBusy(false); updateQuestionMode(); }
}

function renderPendingFiles() {
  elements.pendingFiles.replaceChildren(); elements.pendingFiles.classList.toggle("hidden", !state.files.length);
  state.files.forEach((file, index) => {
    const item = document.createElement("span"); item.className = "pending-file"; item.append(document.createTextNode(file.name));
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "×";
    remove.addEventListener("click", () => { state.files.splice(index, 1); renderPendingFiles(); }); item.append(remove); elements.pendingFiles.append(item);
  });
}

async function submitMessage(event) {
  event.preventDefault();
  const content = elements.input.value.trim();
  if ((!content && !state.files.length) || state.busy) return;
  const sentFiles = [...state.files];
  setBusy(true);
  try {
    if (!state.chat) {
      state.chat = await createChat(apiBase);
      elements.title.textContent = state.chat.name;
    }
    if (sentFiles.length) await uploadDocuments(apiBase, state.chat.id, sentFiles, crypto.randomUUID());
    const actualContent = content || "Проанализируй приложенные документы";
    renderUserMessage(actualContent, sentFiles);
    elements.input.value = ""; state.files = []; resizeInput(); renderPendingFiles();
    if (state.run?.status === "requires_input") {
      const resolved = await answerAnalysisQuestions(apiBase, state.chat.id, state.run.run_id, actualContent);
      state.run = resolved.analysis; renderAnalysis(resolved.analysis); refreshHistory();
      await generateExcel(resolved.analysis);
    } else {
      const accepted = await sendChatMessage(apiBase, state.chat.id, actualContent);
      state.run = accepted.analysis; renderThinking(accepted.analysis); refreshHistory();
      await beginPolling(accepted.analysis.run_id);
    }
  } catch (error) { setBusy(false); showToast(formatApiError(error)); }
}

async function skipQuestions() {
  if (!state.chat || state.run?.status !== "requires_input" || state.busy) return;
  setBusy(true);
  try {
    state.run = await skipAnalysisQuestions(apiBase, state.chat.id, state.run.run_id);
    renderAnalysis(state.run);
    await generateExcel(state.run);
  } catch (error) { showToast(formatApiError(error)); }
  finally { setBusy(false); updateQuestionMode(); }
}

function scrollToBottom() { requestAnimationFrame(() => elements.conversation.scrollTo({ top: elements.conversation.scrollHeight, behavior: "smooth" })); }

elements.composer.addEventListener("submit", submitMessage);
elements.input.addEventListener("input", resizeInput);
elements.input.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); elements.composer.requestSubmit(); } });
elements.attach.addEventListener("click", () => elements.fileInput.click());
elements.fileInput.addEventListener("change", () => { addFiles(elements.fileInput.files); elements.fileInput.value = ""; });
elements.conversation.addEventListener("scroll", updateScrollProgress, { passive: true });
new ResizeObserver(updateScrollProgress).observe(elements.conversation);
document.addEventListener("dragenter", (event) => {
  if (!hasFiles(event)) return;
  event.preventDefault(); dragDepth += 1; elements.dropOverlay.classList.remove("hidden");
});
document.addEventListener("dragover", (event) => { if (hasFiles(event)) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; } });
document.addEventListener("dragleave", (event) => {
  if (!hasFiles(event)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) elements.dropOverlay.classList.add("hidden");
});
document.addEventListener("drop", (event) => {
  if (!hasFiles(event)) return;
  event.preventDefault(); dragDepth = 0; elements.dropOverlay.classList.add("hidden");
  addFiles(event.dataTransfer.files); elements.input.focus();
});
elements.themeToggle.addEventListener("click", () => {
  const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  localStorage.setItem("projectile-theme", theme); setTheme(theme);
});
elements.newChat.addEventListener("click", newChat);
elements.ratesButton.addEventListener("click", openRates);
elements.ratesClose.addEventListener("click", closeRates);
elements.ratesModal.addEventListener("click", (event) => { if (event.target === elements.ratesModal) closeRates(); });
elements.ratesUpload.addEventListener("click", async () => {
  const file = elements.ratesFile.files[0];
  if (!file) { showToast("Выберите файл со ставками."); return; }
  elements.ratesUpload.disabled = true;
  try { renderRateImport(await uploadRateImport(apiBase, file, elements.ratesAuto.checked)); }
  catch (error) { showToast(formatApiError(error)); }
  finally { elements.ratesUpload.disabled = false; }
});
elements.openSidebar.addEventListener("click", () => { elements.sidebar.classList.add("open"); elements.overlay.classList.add("open"); });
elements.closeSidebar.addEventListener("click", closeSidebar); elements.overlay.addEventListener("click", closeSidebar);
window.addEventListener("beforeunload", () => state.pollController?.abort());

setTheme(initialTheme()); loadProjectTypes().finally(newChat);
