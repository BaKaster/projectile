import {
  analysisExcelUrl,
  analysisReportUrl,
  answerAnalysisQuestions,
  createChat,
  deleteChat,
  formatApiError,
  getAnalysisRun,
  getChat,
  listChats,
  sendChatMessage,
  skipAnalysisQuestions,
  updateChat,
  uploadDocuments,
} from "./api.js";
import { ACTIVE_STATUSES, pollAnalysis, SUCCESS_STATUSES } from "./polling.js";

const apiBase = window.PROJECTILE_CONFIG?.apiBase || "http://localhost:8000";
const state = { chat: null, run: null, files: [], busy: false, pollController: null };
const $ = (selector) => document.querySelector(selector);
const elements = {
  sidebar: $("#sidebar"), overlay: $("#sidebar-overlay"), history: $("#chat-history"),
  newChat: $("#new-chat"), openSidebar: $("#open-sidebar"), closeSidebar: $("#close-sidebar"),
  title: $("#chat-title"), subtitle: $("#chat-subtitle"),
  conversation: $("#conversation"), empty: $("#empty-state"),
  composer: $("#composer"), input: $("#message-input"), send: $("#send-button"),
  attach: $("#attach-button"), fileInput: $("#file-input"), pendingFiles: $("#pending-files"),
  questionActions: $("#question-actions"), skip: $("#skip-questions"), toast: $("#toast"),
  themeToggle: $("#theme-toggle"), themeLabel: $("#theme-label"), dropOverlay: $("#drop-overlay"),
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

function closeSidebar() {
  elements.sidebar.classList.remove("open");
  elements.overlay.classList.remove("open");
}

function resizeInput() {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 160)}px`;
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

function statusLabel(status) {
  return ({ queued: "В очереди", extracting: "Читаем документы", analyzing: "Анализируем", requires_input: "Нужны ответы", ready: "Анализ готов", failed: "Ошибка анализа" })[status] || "";
}

function newChat() {
  state.pollController?.abort();
  state.chat = null; state.run = null; state.files = [];
  elements.title.textContent = "Новый чат";
  elements.subtitle.textContent = "AI-анализ проектной документации";
  elements.conversation.replaceChildren(elements.empty);
  elements.empty.classList.remove("hidden");
  elements.questionActions.classList.add("hidden");
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
    for (const message of chat.messages) renderUserMessage(message.content, []);
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

function messageShell(role, label) {
  const message = document.createElement("article");
  message.className = `message ${role}`;
  const avatar = document.createElement("div"); avatar.className = "avatar"; avatar.textContent = role === "user" ? "Вы" : "P";
  const body = document.createElement("div"); body.className = "message-body";
  const meta = document.createElement("div"); meta.className = "message-meta"; meta.textContent = label;
  body.append(meta); message.append(avatar, body); elements.conversation.append(message);
  return body;
}

function renderUserMessage(content, files) {
  elements.empty.classList.add("hidden");
  const body = messageShell("user", "Вы");
  const text = document.createElement("div"); text.className = "message-text"; text.textContent = content; body.append(text);
  if (files.length) {
    const container = document.createElement("div"); container.className = "message-files";
    for (const file of files) { const chip = document.createElement("span"); chip.className = "file-chip"; chip.textContent = file.name; container.append(chip); }
    body.append(container);
  }
}

function renderThinking(run) {
  $("#thinking-message")?.remove();
  const body = messageShell("assistant", "Projectile");
  body.parentElement.id = "thinking-message";
  const row = document.createElement("div"); row.className = "thinking";
  const dots = document.createElement("span"); dots.className = "thinking-dots"; dots.innerHTML = "<i></i><i></i><i></i>";
  const text = document.createElement("span"); text.textContent = statusLabel(run.status) || "Готовим анализ";
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

function workPlanBlock(plan) {
  if (!plan?.packages?.length) return textBlock("План работ пока не сформирован.");
  const container = document.createElement("div"); container.className = "work-plan";
  const totals = document.createElement("div"); totals.className = "work-plan-totals";
  const totalHours = document.createElement("strong"); totalHours.textContent = `${plan.total_effort_hours ?? "—"} чел.-ч`;
  const mode = document.createElement("span"); mode.textContent = plan.estimation_mode === "ai_refined" ? "оценка уточнена моделью" : "расчёт по нормативам";
  totals.append(totalHours, mode); container.append(totals);
  for (const packageItem of plan.packages) {
    const stage = document.createElement("details"); stage.className = "work-stage";
    const heading = document.createElement("summary");
    const stageHours = packageItem.works.reduce((sum, work) => sum + (work.effort_hours || 0), 0);
    heading.textContent = `${packageItem.stage_code} · ${stageHours} чел.-ч`;
    stage.append(heading);
    const works = document.createElement("div"); works.className = "work-items";
    for (const work of packageItem.works) {
      const item = document.createElement("article"); item.className = "work-item";
      const title = document.createElement("strong"); title.textContent = work.name;
      const range = document.createElement("span"); range.className = "work-hours";
      range.textContent = `${work.effort_hours} чел.-ч · ${work.effort_min_hours}–${work.effort_max_hours}`;
      const roles = document.createElement("div"); roles.className = "work-roles";
      for (const role of work.role_assignments || []) {
        const chip = document.createElement("span");
        chip.textContent = `${role.role_name}: ${role.effort_hours} ч`;
        chip.title = role.responsibility; roles.append(chip);
      }
      item.append(title, range, roles); works.append(item);
    }
    stage.append(works); container.append(stage);
  }
  return container;
}

function renderAnalysis(run) {
  $("#thinking-message")?.remove(); $("#analysis-message")?.remove();
  const result = run.result;
  if (!result) { renderFailure([{ message: "Backend завершил анализ без результата" }]); return; }
  state.run = run;
  const body = messageShell("assistant", "Projectile"); body.parentElement.id = "analysis-message";
  const root = document.createElement("div"); root.className = "analysis";
  const top = document.createElement("div"); top.className = "analysis-top";
  const overview = document.createElement("div");
  const label = document.createElement("div"); label.className = "analysis-label"; label.textContent = "Результат анализа";
  const type = document.createElement("div"); type.className = "analysis-type"; type.textContent = result.project_type_code || "Тип не определён";
  const summary = document.createElement("p"); summary.className = "analysis-summary"; summary.textContent = result.summary;
  overview.append(label, type, summary);
  const confidence = document.createElement("span"); confidence.className = "confidence"; confidence.textContent = `Уверенность: ${{ low: "низкая", medium: "средняя", high: "высокая" }[result.confidence]}`;
  top.append(overview, confidence); root.append(top);
  root.append(analysisSection("Обоснование", textBlock(result.rationale)));
  root.append(analysisSection("Факты", gridBlock(result.facts, (fact) => card(fact.name, fact.value))));
  root.append(analysisSection("Допущения", listBlock(result.assumptions)));
  root.append(analysisSection("Проблемы и риски", gridBlock(result.issues, (issue) => card(issue.description, issue.impact_on_estimate))));
  root.append(analysisSection("Этапы, роли и трудозатраты", workPlanBlock(result.work_plan)));
  root.append(analysisSection("Вопросы", gridBlock(result.questions, (question) => card(question.question, question.reason, "question"))));
  root.append(analysisSection("Предупреждения", listBlock(result.warnings)));
  const footer = document.createElement("div"); footer.className = "analysis-footer";
  const identity = document.createElement("span"); identity.className = "analysis-id"; identity.textContent = `Анализ ${run.run_id.slice(0, 8)}`;
  const download = document.createElement("a"); download.className = "download-button";
  download.dataset.reportProject = state.chat.id;
  download.dataset.reportRun = run.run_id;
  download.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m0 0 5-5m-5 5-5-5M5 19h14"/></svg><span>Скачать этот анализ в PDF</span>';
  footer.append(identity, download); root.append(footer); syncReportLinks();
  body.append(root, renderExcelPanel(run));
  updateQuestionMode(); scrollToBottom(); refreshHistory();
}

function renderExcelPanel(run) {
  const panel = document.createElement("section"); panel.className = "excel-panel";
  const button = document.createElement("button"); button.type = "button"; button.className = "excel-button";
  button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h8l4 4v14H7zM15 3v5h4M10 12l4 5m0-5-4 5"/></svg><span>Сформировать Excel файл</span>';
  button.setAttribute("aria-expanded", "false");
  button.addEventListener("click", () => {
    const questions = run.result?.questions?.filter((question) => question.blocking) || [];
    if (run.status === "requires_input" && questions.length) {
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
    const link = document.createElement("a");
    link.href = analysisExcelUrl(apiBase, state.chat.id, run.run_id);
    link.download = `projectile-analysis-${run.run_id}.xlsx`;
    document.body.append(link); link.click(); link.remove();
  });
  panel.append(button);
  return panel;
}

function buildExcelQuestions(run, questions, trigger) {
  const form = document.createElement("form"); form.className = "excel-questions";
  const title = document.createElement("h3"); title.textContent = "Нужна дополнительная информация";
  const copy = document.createElement("p");
  copy.textContent = "Заполните ответы на вопросы ниже. После уточнения анализ обновится, и Excel-файл можно будет сформировать повторным нажатием.";
  const list = document.createElement("div"); list.className = "excel-question-list";
  questions.forEach((question, index) => {
    const field = document.createElement("div"); field.className = "excel-question";
    const label = document.createElement("label"); label.htmlFor = `excel-answer-${index}`; label.textContent = question.question;
    if (question.reason) { const reason = document.createElement("small"); reason.textContent = question.reason; label.append(reason); }
    const input = document.createElement("textarea"); input.id = `excel-answer-${index}`; input.name = `answer-${index}`; input.required = true; input.placeholder = "Введите ответ…";
    field.append(label, input); list.append(field);
  });
  const actions = document.createElement("div"); actions.className = "excel-question-actions";
  const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "excel-cancel"; cancel.textContent = "Отмена";
  const submit = document.createElement("button"); submit.type = "submit"; submit.className = "excel-submit"; submit.textContent = "Отправить ответы";
  cancel.addEventListener("click", () => { trigger.setAttribute("aria-expanded", "false"); form.remove(); });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const answers = questions.map((question, index) => ({ question: question.question, answer: form.elements[`answer-${index}`].value.trim() }));
    const missing = answers.findIndex((item) => !item.answer);
    if (missing >= 0) { form.elements[`answer-${missing}`].focus(); showToast("Ответьте на все вопросы, чтобы обновить анализ."); return; }
    const content = ["Ответы для формирования Excel:", ...answers.map((item, index) => `${index + 1}. ${item.question}\nОтвет: ${item.answer}`)].join("\n\n");
    setBusy(true); submit.disabled = true; cancel.disabled = true;
    try {
      renderUserMessage(content, []);
      const accepted = await answerAnalysisQuestions(apiBase, state.chat.id, run.run_id, content);
      state.run = accepted.analysis; renderThinking(accepted.analysis); refreshHistory();
      await beginPolling(accepted.analysis.run_id);
    } catch (error) { setBusy(false); submit.disabled = false; cancel.disabled = false; showToast(formatApiError(error)); }
  });
  actions.append(cancel, submit); form.append(title, copy, list, actions);
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
  elements.questionActions.classList.toggle("hidden", !requires);
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
      state.chat = await createChat(apiBase, content.slice(0, 80) || "Анализ документов");
      elements.title.textContent = state.chat.name;
    }
    if (sentFiles.length) await uploadDocuments(apiBase, state.chat.id, sentFiles, crypto.randomUUID());
    const actualContent = content || "Проанализируй приложенные документы";
    renderUserMessage(actualContent, sentFiles);
    elements.input.value = ""; state.files = []; resizeInput(); renderPendingFiles();
    const accepted = state.run?.status === "requires_input"
      ? await answerAnalysisQuestions(apiBase, state.chat.id, state.run.run_id, actualContent)
      : await sendChatMessage(apiBase, state.chat.id, actualContent);
    state.run = accepted.analysis; renderThinking(accepted.analysis); refreshHistory();
    await beginPolling(accepted.analysis.run_id);
  } catch (error) { setBusy(false); showToast(formatApiError(error)); }
}

async function skipQuestions() {
  if (!state.chat || state.run?.status !== "requires_input" || state.busy) return;
  setBusy(true);
  try {
    state.run = await skipAnalysisQuestions(apiBase, state.chat.id, state.run.run_id);
    renderAnalysis(state.run);
  } catch (error) { showToast(formatApiError(error)); }
  finally { setBusy(false); updateQuestionMode(); }
}

function scrollToBottom() { requestAnimationFrame(() => elements.conversation.scrollTo({ top: elements.conversation.scrollHeight, behavior: "smooth" })); }

elements.composer.addEventListener("submit", submitMessage);
elements.input.addEventListener("input", resizeInput);
elements.input.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); elements.composer.requestSubmit(); } });
elements.attach.addEventListener("click", () => elements.fileInput.click());
elements.fileInput.addEventListener("change", () => { addFiles(elements.fileInput.files); elements.fileInput.value = ""; });
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
elements.newChat.addEventListener("click", newChat); elements.skip.addEventListener("click", skipQuestions);
elements.openSidebar.addEventListener("click", () => { elements.sidebar.classList.add("open"); elements.overlay.classList.add("open"); });
elements.closeSidebar.addEventListener("click", closeSidebar); elements.overlay.addEventListener("click", closeSidebar);
window.addEventListener("beforeunload", () => state.pollController?.abort());

setTheme(initialTheme()); newChat();
