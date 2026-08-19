import {
  ApiError,
  createProject,
  formatApiError,
  getAnalysisRun,
  getHealth,
  getLatestAnalysis,
  startAnalysis,
  uploadDocuments,
} from "./api.js";
import { ACTIVE_STATUSES, pollAnalysis, SUCCESS_STATUSES } from "./polling.js";

const apiBase = window.PROJECTILE_CONFIG?.apiBase || "http://localhost:8000";
const storageKey = "projectile.activeProject";

const state = {
  project: loadProject(),
  files: [],
  pollController: null,
};

const elements = {
  views: [...document.querySelectorAll("main > .workspace")],
  steps: [...document.querySelectorAll(".step")],
  apiState: document.querySelector("#api-state span:last-child"),
  projectForm: document.querySelector("#project-form"),
  projectName: document.querySelector("#project-name"),
  projectError: document.querySelector("#project-error"),
  activeProjectName: document.querySelector("#active-project-name"),
  newProjectButton: document.querySelector("#new-project-button"),
  dropZone: document.querySelector("#drop-zone"),
  chooseFiles: document.querySelector("#choose-files"),
  chooseFolder: document.querySelector("#choose-folder"),
  fileInput: document.querySelector("#file-input"),
  folderInput: document.querySelector("#folder-input"),
  selection: document.querySelector("#selection"),
  selectionCount: document.querySelector("#selection-count"),
  fileList: document.querySelector("#file-list"),
  clearFiles: document.querySelector("#clear-files"),
  analyzeButton: document.querySelector("#analyze-button"),
  uploadError: document.querySelector("#upload-error"),
  progressTitle: document.querySelector("#progress-title"),
  progressDescription: document.querySelector("#progress-description"),
  stages: [...document.querySelectorAll(".stage")],
  resultTitle: document.querySelector("#result-title"),
  resultContent: document.querySelector("#result-content"),
  repeatAnalysis: document.querySelector("#repeat-analysis"),
  resultNewProject: document.querySelector("#result-new-project"),
  failureErrors: document.querySelector("#failure-errors"),
  retryAnalysis: document.querySelector("#retry-analysis"),
  toast: document.querySelector("#toast"),
};

elements.apiState.textContent = `API: ${apiBase.replace(/^https?:\/\//, "")}`;

async function checkApiHealth() {
  const dot = document.querySelector(".status-dot");
  try {
    const health = await getHealth(apiBase);
    dot.classList.toggle("offline", health.status !== "ok" || health.database !== "ok");
    elements.apiState.title = "Backend доступен";
  } catch {
    dot.classList.add("offline");
    elements.apiState.title = "Backend недоступен";
  }
}

function loadProject() {
  try {
    return JSON.parse(localStorage.getItem(storageKey)) || null;
  } catch {
    return null;
  }
}

function saveProject(project) {
  state.project = { id: project.id, name: project.name };
  localStorage.setItem(storageKey, JSON.stringify(state.project));
}

function showView(id, step) {
  for (const view of elements.views) view.classList.toggle("hidden", view.id !== id);
  for (const item of elements.steps) {
    const itemStep = Number(item.dataset.step);
    item.classList.toggle("active", itemStep === step);
    item.classList.toggle("done", itemStep < step);
  }
  document.querySelector(`#${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function setBusy(button, busy, label) {
  if (!button.dataset.label) button.dataset.label = button.innerHTML;
  button.disabled = busy;
  button.innerHTML = busy ? label : button.dataset.label;
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.remove("hidden");
  window.setTimeout(() => elements.toast.classList.add("hidden"), 3500);
}

function resetProject() {
  state.pollController?.abort();
  state.project = null;
  state.files = [];
  localStorage.removeItem(storageKey);
  elements.projectForm.reset();
  renderFiles();
  showView("create-view", 1);
}

function showUpload() {
  elements.activeProjectName.textContent = state.project.name;
  elements.uploadError.textContent = "";
  showView("upload-view", 2);
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} КБ`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} МБ`;
  return `${(bytes / 1024 ** 3).toFixed(1)} ГБ`;
}

function fileExtension(filename) {
  const extension = filename.split(".").pop();
  return extension && extension !== filename ? extension.slice(0, 4).toUpperCase() : "FILE";
}

function addFiles(fileList) {
  const incoming = [...fileList];
  if (!incoming.length) return;
  const known = new Set(state.files.map((file) => `${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`));
  for (const file of incoming) {
    const key = `${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`;
    if (!known.has(key)) {
      state.files.push(file);
      known.add(key);
    }
  }
  renderFiles();
}

function renderFiles() {
  elements.selection.classList.toggle("hidden", state.files.length === 0);
  elements.selectionCount.textContent = `${state.files.length} ${pluralize(state.files.length, "документ", "документа", "документов")}`;
  elements.fileList.replaceChildren();

  for (const file of state.files.slice(0, 100)) {
    const item = document.createElement("li");
    const badge = document.createElement("span");
    badge.className = "file-badge";
    badge.textContent = fileExtension(file.name);
    const name = document.createElement("span");
    name.className = "file-name";
    name.textContent = file.webkitRelativePath || file.name;
    name.title = name.textContent;
    const size = document.createElement("span");
    size.className = "file-size";
    size.textContent = formatSize(file.size);
    item.append(badge, name, size);
    elements.fileList.append(item);
  }
  if (state.files.length > 100) {
    const item = document.createElement("li");
    item.textContent = `И ещё ${state.files.length - 100}…`;
    elements.fileList.append(item);
  }
}

function pluralize(number, one, few, many) {
  const lastTwo = number % 100;
  const last = number % 10;
  if (lastTwo >= 11 && lastTwo <= 19) return many;
  if (last === 1) return one;
  if (last >= 2 && last <= 4) return few;
  return many;
}

function updateProgress(run) {
  const order = ["queued", "extracting", "analyzing"];
  const currentIndex = Math.max(0, order.indexOf(run.status));
  const titles = {
    queued: "Готовим документы",
    extracting: "Извлекаем данные",
    analyzing: "Собираем картину проекта",
  };
  elements.progressTitle.textContent = titles[run.status] || "Изучаем документы";
  if (run.current_step && run.current_step !== run.status) {
    elements.progressDescription.textContent = `Текущий этап: ${run.current_step}. Длительная обработка сканов и аудио — нормальна.`;
  }
  for (const stage of elements.stages) {
    const index = order.indexOf(stage.dataset.status);
    stage.classList.toggle("complete", index < currentIndex);
    stage.classList.toggle("current", index === currentIndex);
  }
}

async function beginPolling(runId, initialRun) {
  state.pollController?.abort();
  state.pollController = new AbortController();
  showView("progress-view", 3);
  if (initialRun) updateProgress(initialRun);

  try {
    const run = await pollAnalysis({
      fetchRun: (signal) => getAnalysisRun(apiBase, state.project.id, runId, signal),
      onUpdate: updateProgress,
      signal: state.pollController.signal,
    });
    if (!run) return;
    if (SUCCESS_STATUSES.has(run.status)) renderResult(run);
    else if (run.status === "failed") renderFailure(run.errors);
  } catch (error) {
    if (error.name !== "AbortError") {
      showToast(formatApiError(error));
      window.setTimeout(() => {
        if (state.project) restoreLatest();
      }, 5000);
    }
  }
}

async function runAnalysis() {
  showView("progress-view", 3);
  updateProgress({ status: "queued", current_step: "queued" });
  try {
    const accepted = await startAnalysis(apiBase, state.project.id);
    await beginPolling(accepted.run_id, accepted);
  } catch (error) {
    elements.uploadError.textContent = formatApiError(error);
    showUpload();
  }
}

async function restoreLatest() {
  const controller = new AbortController();
  state.pollController = controller;
  try {
    const run = await getLatestAnalysis(apiBase, state.project.id, controller.signal);
    if (ACTIVE_STATUSES.has(run.status)) await beginPolling(run.run_id, run);
    else if (SUCCESS_STATUSES.has(run.status)) renderResult(run);
    else renderFailure(run.errors);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) showUpload();
    else {
      showUpload();
      elements.uploadError.textContent = formatApiError(error);
    }
  }
}

function section(title, content) {
  const container = document.createElement("section");
  container.className = "result-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  container.append(heading, content);
  return container;
}

function paragraph(text, className = "") {
  const element = document.createElement("p");
  element.className = className;
  element.textContent = text;
  return element;
}

function bulletList(items, emptyText) {
  if (!items?.length) return paragraph(emptyText, "empty-copy");
  const list = document.createElement("ul");
  list.className = "bullet-list";
  for (const text of items) {
    const item = document.createElement("li");
    item.textContent = text;
    list.append(item);
  }
  return list;
}

function cards(items, makeCard, emptyText) {
  if (!items?.length) return paragraph(emptyText, "empty-copy");
  const container = document.createElement("div");
  container.className = "cards";
  for (const item of items) container.append(makeCard(item));
  return container;
}

function dataCard(title, text, meta = "", className = "") {
  const card = document.createElement("article");
  card.className = `data-card ${className}`.trim();
  const heading = document.createElement("strong");
  heading.textContent = title;
  card.append(heading, paragraph(text));
  if (meta) {
    const small = document.createElement("small");
    small.textContent = meta;
    card.append(small);
  }
  return card;
}

function renderResult(run) {
  const result = run.result;
  if (!result) {
    renderFailure([{ message: "Backend завершил анализ без результата" }]);
    return;
  }
  showView("result-view", 4);
  elements.resultTitle.textContent = run.status === "requires_input" ? "Нужны уточнения" : "Проект разобран";
  elements.resultContent.replaceChildren();

  if (run.status === "requires_input") {
    elements.resultContent.append(paragraph(
      "Для точной оценки не хватает существенных данных. Ответы пока нельзя отправить через интерфейс — endpoint ещё не реализован.",
      "requires-banner",
    ));
  }

  const overview = document.createElement("div");
  overview.className = "result-overview";
  const main = document.createElement("div");
  const label = document.createElement("span");
  label.className = "kicker";
  label.textContent = "Тип проекта";
  const type = document.createElement("div");
  type.className = "type-code";
  type.textContent = result.project_type_code || "Не определён однозначно";
  main.append(label, type, paragraph(result.summary, "summary"));
  const confidenceCard = document.createElement("div");
  confidenceCard.className = "confidence-card";
  const confidenceLabel = document.createElement("small");
  confidenceLabel.textContent = "Уверенность модели";
  const confidence = document.createElement("span");
  confidence.className = `confidence ${result.confidence}`;
  confidence.textContent = ({ low: "Низкая", medium: "Средняя", high: "Высокая" })[result.confidence] || result.confidence;
  confidenceCard.append(confidenceLabel, confidence);
  overview.append(main, confidenceCard);
  elements.resultContent.append(overview);

  elements.resultContent.append(section("Почему сделан такой вывод", paragraph(result.rationale)));
  elements.resultContent.append(section("Факты", cards(
    result.facts,
    (fact) => dataCard(fact.name, fact.value, fact.source_document_ids?.length ? `Источники: ${fact.source_document_ids.join(", ")}` : ""),
    "Подтверждённые факты не выделены.",
  )));
  elements.resultContent.append(section("Допущения", bulletList(result.assumptions, "Допущений нет.")));
  elements.resultContent.append(section("Проблемы и риски", cards(
    result.issues,
    (issue) => dataCard(issue.description, issue.impact_on_estimate, issue.recommended_action ? `Что сделать: ${issue.recommended_action}` : issue.code, `issue-card ${issue.severity}`),
    "Существенных проблем не обнаружено.",
  )));
  elements.resultContent.append(section("Пробелы в данных", cards(
    result.gaps,
    (gap) => dataCard(gap.description, gap.suggested_assumption || "Допущение не предложено", `Влияние: ${gap.impact}${gap.blocking ? " · блокирует оценку" : ""}`, `issue-card ${gap.impact}`),
    "Критичных пробелов в данных нет.",
  )));
  elements.resultContent.append(section("Вопросы", cards(
    result.questions,
    (question) => dataCard(question.question, question.reason, question.blocking ? "Нужен ответ для продолжения" : "Влияет на точность", "question-card"),
    "Дополнительных вопросов нет.",
  )));
  elements.resultContent.append(section("Предупреждения", bulletList(result.warnings, "Предупреждений нет.")));
}

function renderFailure(errors = []) {
  showView("failed-view", 3);
  elements.failureErrors.replaceChildren();
  const normalized = errors.length ? errors : [{ message: "Backend не передал детали ошибки" }];
  for (const error of normalized) {
    const line = document.createElement("div");
    line.textContent = typeof error === "string" ? error : error.message || error.detail || JSON.stringify(error);
    elements.failureErrors.append(line);
  }
}

elements.projectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = elements.projectName.value.trim();
  if (!name) return;
  const button = elements.projectForm.querySelector("button");
  elements.projectError.textContent = "";
  setBusy(button, true, "Создаём…");
  try {
    const project = await createProject(apiBase, name);
    saveProject(project);
    showUpload();
  } catch (error) {
    elements.projectError.textContent = formatApiError(error);
  } finally {
    setBusy(button, false);
  }
});

elements.chooseFiles.addEventListener("click", (event) => { event.stopPropagation(); elements.fileInput.click(); });
elements.chooseFolder.addEventListener("click", (event) => { event.stopPropagation(); elements.folderInput.click(); });
elements.dropZone.addEventListener("click", () => elements.fileInput.click());
elements.dropZone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") elements.fileInput.click();
});
elements.fileInput.addEventListener("change", () => { addFiles(elements.fileInput.files); elements.fileInput.value = ""; });
elements.folderInput.addEventListener("change", () => { addFiles(elements.folderInput.files); elements.folderInput.value = ""; });
for (const eventName of ["dragenter", "dragover"]) {
  elements.dropZone.addEventListener(eventName, (event) => { event.preventDefault(); elements.dropZone.classList.add("dragging"); });
}
for (const eventName of ["dragleave", "drop"]) {
  elements.dropZone.addEventListener(eventName, (event) => { event.preventDefault(); elements.dropZone.classList.remove("dragging"); });
}
elements.dropZone.addEventListener("drop", (event) => addFiles(event.dataTransfer.files));
elements.clearFiles.addEventListener("click", () => { state.files = []; renderFiles(); });
elements.newProjectButton.addEventListener("click", resetProject);
elements.resultNewProject.addEventListener("click", resetProject);
elements.repeatAnalysis.addEventListener("click", runAnalysis);
elements.retryAnalysis.addEventListener("click", runAnalysis);

elements.analyzeButton.addEventListener("click", async () => {
  if (!state.files.length) return;
  elements.uploadError.textContent = "";
  setBusy(elements.analyzeButton, true, "Загружаем документы…");
  try {
    await uploadDocuments(apiBase, state.project.id, state.files, crypto.randomUUID());
    state.files = [];
    renderFiles();
    await runAnalysis();
  } catch (error) {
    elements.uploadError.textContent = formatApiError(error);
  } finally {
    setBusy(elements.analyzeButton, false);
  }
});

window.addEventListener("beforeunload", () => state.pollController?.abort());

if (state.project?.id) {
  elements.activeProjectName.textContent = state.project.name || "Без названия";
  restoreLatest();
} else {
  showView("create-view", 1);
}

checkApiHealth();
