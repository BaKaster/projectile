import assert from "node:assert/strict";
import test from "node:test";
import {
  ApiError,
  buildUploadFormData,
  createProject,
  getLatestAnalysis,
  uploadDocuments,
} from "../api.js";

function mockResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("buildUploadFormData adds repeated files without paths for a regular selection", () => {
  const files = [new File(["one"], "ТЗ.pdf"), new File(["two"], "смета.xlsx")];
  const form = buildUploadFormData(files);

  assert.deepEqual(form.getAll("files").map((file) => file.name), ["ТЗ.pdf", "смета.xlsx"]);
  assert.deepEqual(form.getAll("relative_paths"), []);
});

test("buildUploadFormData adds a matching relative path for every folder file", () => {
  const first = new File(["one"], "ТЗ.pdf");
  const second = new File(["two"], "смета.xlsx");
  Object.defineProperty(first, "webkitRelativePath", { value: "Проект/ТЗ.pdf" });
  Object.defineProperty(second, "webkitRelativePath", { value: "Проект/Финансы/смета.xlsx" });

  const form = buildUploadFormData([first, second]);
  assert.equal(form.getAll("files").length, 2);
  assert.deepEqual(form.getAll("relative_paths"), ["Проект/ТЗ.pdf", "Проект/Финансы/смета.xlsx"]);
});

test("buildUploadFormData omits all relative paths when only some files have them", () => {
  const first = new File(["one"], "ТЗ.pdf");
  const second = new File(["two"], "смета.xlsx");
  Object.defineProperty(first, "webkitRelativePath", { value: "Проект/ТЗ.pdf" });

  assert.deepEqual(buildUploadFormData([first, second]).getAll("relative_paths"), []);
});

test("API calls use the backend contract and preserve FormData boundary", async (context) => {
  const calls = [];
  context.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, options });
    return mockResponse({ id: "project-id", name: "Тест" }, options.method === "POST" ? 201 : 200);
  });

  await createProject("http://localhost:8000/", "Тест");
  await uploadDocuments("http://localhost:8000", "project-id", [new File(["x"], "a.txt")], "idem-key");

  assert.equal(calls[0].url, "http://localhost:8000/api/v1/projects");
  assert.deepEqual(JSON.parse(calls[0].options.body), { name: "Тест" });
  assert.equal(calls[1].options.headers["Idempotency-Key"], "idem-key");
  assert.equal(calls[1].options.headers["Content-Type"], undefined);
  assert.ok(calls[1].options.body instanceof FormData);
});

test("latest analysis exposes a 404 as ApiError for restoration logic", async (context) => {
  context.mock.method(globalThis, "fetch", async () => mockResponse({ detail: "Project analysis not found" }, 404));
  await assert.rejects(
    getLatestAnalysis("http://localhost:8000", "project-id"),
    (error) => error instanceof ApiError && error.status === 404,
  );
});
