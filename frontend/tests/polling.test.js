import assert from "node:assert/strict";
import test from "node:test";
import { pollAnalysis } from "../polling.js";

test("polling continues through active states and stops at ready", async () => {
  const runs = [
    { status: "queued" },
    { status: "extracting" },
    { status: "analyzing" },
    { status: "ready", result: {} },
  ];
  const updates = [];
  const result = await pollAnalysis({
    fetchRun: async () => runs.shift(),
    onUpdate: (run) => updates.push(run.status),
    wait: async () => {},
  });
  assert.equal(result.status, "ready");
  assert.deepEqual(updates, ["queued", "extracting", "analyzing", "ready"]);
});

test("requires_input is a successful terminal state", async () => {
  let calls = 0;
  const result = await pollAnalysis({
    fetchRun: async () => { calls += 1; return { status: "requires_input", result: {} }; },
    onUpdate: () => {},
    wait: async () => {},
  });
  assert.equal(result.status, "requires_input");
  assert.equal(calls, 1);
});

test("failed is terminal and returns backend errors", async () => {
  const result = await pollAnalysis({
    fetchRun: async () => ({ status: "failed", errors: [{ message: "OCR failed" }] }),
    onUpdate: () => {},
    wait: async () => {},
  });
  assert.deepEqual(result.errors, [{ message: "OCR failed" }]);
});

test("an aborted polling loop does not issue a request", async () => {
  const controller = new AbortController();
  controller.abort();
  let called = false;
  const result = await pollAnalysis({
    fetchRun: async () => { called = true; },
    onUpdate: () => {},
    signal: controller.signal,
    wait: async () => {},
  });
  assert.equal(result, null);
  assert.equal(called, false);
});
