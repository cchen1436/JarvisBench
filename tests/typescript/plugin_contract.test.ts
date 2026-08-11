import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectEventSchema = JSON.parse(
  fs.readFileSync(
    path.resolve(testDirectory, "../../configs/schemas/project-event.schema.json"),
    "utf8",
  ),
);

function assertObjectContract(
  value: unknown,
  schema: Record<string, any>,
): asserts value is Record<string, unknown> {
  assert.ok(value !== null && typeof value === "object" && !Array.isArray(value));
  const record = value as Record<string, unknown>;
  for (const field of schema.required || []) {
    assert.ok(Object.hasOwn(record, field), `missing schema field ${field}`);
  }
  if (schema.additionalProperties === false) {
    assert.deepEqual(
      Object.keys(record).filter((field) => !(field in schema.properties)),
      [],
    );
  }
  for (const [field, specification] of Object.entries<Record<string, any>>(
    schema.properties || {},
  )) {
    if (!Object.hasOwn(record, field)) continue;
    const item = record[field];
    if (specification.type === "object") {
      assertObjectContract(item, specification);
    } else if (specification.type === "integer") {
      assert.equal(Number.isInteger(item), true, `${field} is not an integer`);
      assert.ok(Number(item) >= Number(specification.minimum || 0));
      if (specification.maximum !== undefined) {
        assert.ok(Number(item) <= Number(specification.maximum));
      }
    } else if (specification.type === "string") {
      assert.equal(typeof item, "string", `${field} is not text`);
      assert.ok(String(item).length >= Number(specification.minLength || 0));
      if (specification.maxLength !== undefined) {
        assert.ok(String(item).length <= Number(specification.maxLength));
      }
      if (specification.pattern !== undefined) {
        assert.match(String(item), new RegExp(String(specification.pattern)));
      }
      if (specification.format === "date-time") {
        assert.equal(Number.isNaN(Date.parse(String(item))), false);
      }
    }
    if (specification.enum !== undefined) {
      assert.ok(specification.enum.includes(item), `${field} is outside its enum`);
    }
  }
}

function assertProjectEventEnvelope(value: unknown): void {
  assertObjectContract(value, projectEventSchema);
}

const root = fs.mkdtempSync(path.join(os.tmpdir(), "jarvisbench-plugin-"));
const controlRoot = path.join(root, "control");
const registryPath = path.join(controlRoot, "registry.json");
const eventPath = path.join(root, "events", "project_events.jsonl");
const readyPath = path.join(controlRoot, "plugin_ready.json");
const projectId = "project-1";
const sessionId = "child-1";
const sessionKey = "agent:main:subagent:child-1";
const siblingId = "child-2";
const siblingKey = "agent:main:subagent:child-2";

process.env.JARVIS_MAS_PROJECT_ID = projectId;
process.env.JARVIS_MAS_CONTROL_ROOT = controlRoot;
process.env.JARVIS_MAS_REGISTRY_JSON = registryPath;
process.env.JARVIS_HOOK_EVENTS_JSONL = eventPath;
process.env.JARVIS_MAS_PLUGIN_READY_JSON = readyPath;
process.env.JARVIS_MAS_PARENT_RUNTIME_SESSION_ID = "chat";
process.env.JARVIS_MAS_PARENT_SESSION_KEY = "agent:main:chat";
process.env.JARVIS_AUTONOMOUS_REVIEW = "1";
process.env.JARVIS_MAS_DYNAMIC_REQUIRED = "1";
process.env.JARVIS_MAS_PLUGIN_ROLE = "gateway";
process.env.JARVIS_BATCH_SETTLE_MS = "5";
process.env.JARVIS_REVIEW_POLL_MS = "25";
process.env.JARVIS_REVIEW_TIMEOUT_MS = "3000";
process.env.JARVIS_REGISTRATION_WAIT_MS = "50";

fs.mkdirSync(controlRoot, { recursive: true });
fs.writeFileSync(
  registryPath,
  JSON.stringify({
    schema_version: "1.0",
    kind: "dynamic_child_registry",
    project_id: projectId,
    parent_session_id: "chat",
    parent_session_key: "agent:main:chat",
    sessions: {
      chat: {
        project_id: projectId,
        agent_id: "parent",
        session_id: "chat",
        session_key: "agent:main:chat",
        parent_id: "parent",
        role: "parent",
        workstream_id: "",
        status: "active",
      },
      [sessionId]: {
        project_id: projectId,
        agent_id: "worker-1",
        session_id: sessionId,
        session_key: sessionKey,
        parent_id: "parent",
        role: "worker",
        workstream_id: "analysis",
        status: "active",
      },
      [siblingId]: {
        project_id: projectId,
        agent_id: "worker-2",
        session_id: siblingId,
        session_key: siblingKey,
        parent_id: "parent",
        role: "worker",
        workstream_id: "delivery",
        status: "active",
      },
    },
    aliases: {
      chat: "chat",
      "agent:main:chat": "chat",
      [sessionId]: sessionId,
      [sessionKey]: sessionId,
      [siblingId]: siblingId,
      [siblingKey]: siblingId,
    },
  }),
);

const pluginModule = await import(
  "../../plugins/openclaw/jarvis_supervisor/index.ts"
);
const execModule = await import(
  "../../plugins/openclaw/jarvis_supervisor/read_only_exec.ts"
);

function writeControl(
  value: Record<string, unknown>,
  exactSessionId = sessionId,
): void {
  const namespace = pluginModule.masSessionNamespace(exactSessionId);
  const directory = path.join(controlRoot, "sessions", namespace);
  fs.mkdirSync(directory, { recursive: true });
  const target = path.join(directory, "control.json");
  const temporary = `${target}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value)}\n`, { mode: 0o600 });
  fs.renameSync(temporary, target);
}

function baseControl(
  exactSessionId = sessionId,
  exactSessionKey = sessionKey,
  agentId = "worker-1",
): Record<string, unknown> {
  return {
    schema_version: "1.0",
    kind: "dynamic_session_control",
    protocol_version: "1.0-release",
    revision: 1,
    project_id: projectId,
    agent_id: agentId,
    session_id: exactSessionId,
    session_key: exactSessionKey,
    parent_id: "parent",
    role: "worker",
    control_epoch: 0,
    nonce: "a".repeat(48),
    pause: { active: false, reason: "", source: "" },
    active_review: null,
    review_responses: [],
    invalidations: [],
    guidance_queue: [],
    delivery_receipts: [],
    application_receipts: [],
  };
}

async function waitForReview(): Promise<Record<string, any>> {
  const deadline = Date.now() + 2000;
  while (Date.now() < deadline) {
    if (fs.existsSync(eventPath)) {
      const events = fs
        .readFileSync(eventPath, "utf8")
        .split("\n")
        .filter(Boolean)
        .map((line) => JSON.parse(line));
      const review = events.find((event) => event.type === "jarvis.review.requested");
      if (review) return review;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error("review event did not arrive");
}

async function waitForReviewForTool(
  toolCallId: string,
): Promise<Record<string, any>> {
  const deadline = Date.now() + 2000;
  while (Date.now() < deadline) {
    if (fs.existsSync(eventPath)) {
      const review = fs
        .readFileSync(eventPath, "utf8")
        .split("\n")
        .filter(Boolean)
        .map((line) => JSON.parse(line))
        .find(
          (event) =>
            event.type === "jarvis.review.requested" &&
            event.payload.held_actions?.some(
              (action: Record<string, any>) =>
                action.tool_call_id === toolCallId,
            ),
        );
      if (review) return review;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`review did not arrive for ${toolCallId}`);
}

async function heldActionFor(
  toolName: string,
  toolCallId: string,
  params: Record<string, unknown>,
): Promise<Record<string, any>> {
  writeControl(baseControl());
  const handlers = new Map<string, Function>();
  pluginModule.default.register({
    source: `parity-${toolCallId}`,
    registerTool() {},
    on(name: string, handler: Function) {
      handlers.set(name, handler);
    },
  });
  const beforeTool = handlers.get("before_tool_call");
  assert.ok(beforeTool);
  const held = beforeTool!(
    { toolName, toolCallId, params },
    { sessionId, runId: `run-${toolCallId}` },
  );
  const review = await waitForReviewForTool(toolCallId);
  const control = baseControl();
  control.review_responses = [
    {
      schema_version: "1.0",
      kind: "review_response",
      control_id: `control-${toolCallId}`,
      project_id: projectId,
      run_id: review.payload.run_id,
      session_id: sessionId,
      turn_id: review.payload.turn_id,
      review_id: review.payload.review_id,
      batch_id: review.payload.batch_id,
      action_ids: review.payload.held_actions.map(
        (item: Record<string, any>) => item.action_id,
      ),
      action_fingerprints: review.payload.held_actions.map(
        (item: Record<string, any>) => item.action_fingerprint,
      ),
      control_epoch: 0,
      next_control_epoch: 0,
      nonce: "a".repeat(48),
      next_nonce: "a".repeat(48),
      expected_event_seq: review.payload.expected_event_seq,
      decision: "allow",
      decision_id: "",
      guidance: "",
      guidance_sha256: "",
      created_at: new Date().toISOString(),
    },
  ];
  writeControl(control);
  assert.equal(await held, undefined);
  assert.equal(review.payload.held_actions.length, 1);
  return review.payload.held_actions[0];
}

test("read-only prefilter fails closed on shell mutation", () => {
  assert.equal(
    execModule.classifyReadOnlyExec("exec", { command: "rg needle src" }).readOnly,
    true,
  );
  assert.equal(
    execModule.classifyReadOnlyExec("exec", { command: "rg needle src > out.txt" })
      .readOnly,
    false,
  );
  assert.equal(pluginModule.classifyAction("write", { path: "results/a" }).consequential, true);
  assert.equal(pluginModule.classifyAction("read", { path: "public/a" }).consequential, false);
});

test("final record intent requires a direct mutation target", async () => {
  const referenceOnly = {
    command:
      "python3 -m json.tool /workspace/results/final.json >/tmp/final-check.txt",
  };
  assert.deepEqual(
    pluginModule.directArtifactPaths("exec", referenceOnly),
    [],
  );
  assert.equal(
    pluginModule.classifyAction("exec", referenceOnly).consequential,
    false,
  );
  const aliasedWrite = {
    command:
      "p=Path('/workspace/results/final.json'); payload='{}'; p.write_text(payload)",
  };
  assert.deepEqual(
    pluginModule.directArtifactPaths("exec", aliasedWrite),
    ["results/final.json"],
  );
  assert.equal(
    pluginModule.classifyAction("exec", aliasedWrite).consequential,
    true,
  );
  const longPythonFinal = {
    command: [
      "python3 - <<'PY'",
      "from pathlib import Path",
      `noise = '${"x".repeat(1800)}'`,
      "obj = {'decision': {'options_considered': ['Vendor D with exception', 'Vendor A without exception'], 'choice': 'Vendor A without exception', 'rationale': 'avoid advance payment risk', 'question_for_requester': 'Choose Vendor D with an exception or Vendor A without one?'}}",
      `tail = '${"y".repeat(1800)}'`,
      "p = Path('/workspace/results/final.json')",
      "p.write_text(json.dumps(obj))",
      "PY",
    ].join("\n"),
  };
  const longSalient = pluginModule.paramsSalientPreview(longPythonFinal);
  assert.match(longSalient, /choice:.*Vendor A without exception/);
  assert.match(longSalient, /question_for_requester:.*Vendor D with an exception/);
  assert.deepEqual(pluginModule.directArtifactPaths("exec", longPythonFinal), [
    "results/final.json",
  ]);

  const written = await heldActionFor("write", "call-final-direct-write", {
    path: "/workspace/results/final.json",
    content: '{"choice":"provisional"}',
  });
  assert.equal(written.final_record_intent, true);
  assert.deepEqual(written.artifact_paths, ["results/final.json"]);

  const edited = await heldActionFor("edit", "call-final-direct-edit", {
    path: "/workspace/results/final.json",
    oldText: '"choice":"provisional"',
    newText: '"choice":"accepted"',
  });
  assert.equal(edited.final_record_intent, true);
  assert.deepEqual(edited.artifact_paths, ["results/final.json"]);

  const intermediate = await heldActionFor(
    "write",
    "call-intermediate-direct-write",
    {
      path: "/workspace/results/recommendation.md",
      content: "A reversible intermediate recommendation.",
    },
  );
  assert.equal(intermediate.final_record_intent, false);
  assert.deepEqual(intermediate.artifact_paths, ["results/recommendation.md"]);
});

test("held review preserves bounded head-tail and decision-salient fields", async () => {
  const content = JSON.stringify({
    selected_option: "requester-facing choice",
    rationale: "public evidence supports a provisional outcome",
    body: `HEAD-${"x".repeat(1800)}-TAIL`,
  });
  const action = await heldActionFor("write", "call-bounded-final-write", {
    path: "/workspace/results/final.json",
    content,
  });

  assert.equal(action.final_record_intent, true);
  assert.equal(action.params_truncated, true);
  assert.ok(action.params_chars > action.params_preview.length);
  assert.match(action.params_preview, /bounded preview of/);
  assert.match(action.params_preview, /HEAD-/);
  assert.match(action.params_preview, /-TAIL/);
  assert.match(action.params_salient_preview, /selected_option/);
  assert.match(action.params_salient_preview, /rationale/);
});

test("decision salience prioritizes choice and requester question over assumptions", () => {
  const question = "Which funding priority should the final action plan use?";
  const salient = pluginModule.paramsSalientPreview({
    path: "/workspace/results/final.json",
    content: JSON.stringify({
      decision: {
        assumptions: ["generic assumption ".repeat(90)],
        choice: (
          `Synthesized plan ${"x".repeat(150)} `
          + "Fund restore and rollback drills before the other controls. "
          + `Preserve provenance ${"y".repeat(150)}`
        ),
        rationale: "public evidence ".repeat(60),
        question_for_requester: question,
      },
    }),
  });

  assert.ok(salient.length <= 900);
  assert.match(salient, /Fund restore and rollback drills/);
  assert.match(salient, new RegExp(question.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.ok(salient.indexOf("choice:") < salient.indexOf("question_for_requester:"));
  assert.ok(salient.indexOf("question_for_requester:") < salient.indexOf("assumptions:"));
});

test("dynamic registration and control namespaces are exact per session", () => {
  const sibling = baseControl(siblingId, siblingKey, "worker-2");
  writeControl(baseControl());
  writeControl(sibling, siblingId);
  assert.notEqual(
    pluginModule.masSessionNamespace(sessionId),
    pluginModule.masSessionNamespace(siblingId),
  );
  assert.equal(pluginModule.resolveMasSessionIdentity(siblingKey)?.session_id, siblingId);
  assert.equal(pluginModule.resolveMasSessionIdentity("unknown"), null);
  assert.equal(pluginModule.validateSessionControl(baseControl(), sessionId), true);
  assert.equal(pluginModule.validateSessionControl(baseControl(), siblingId), false);
  assert.equal(pluginModule.validateSessionControl(sibling, siblingId), true);
  assert.equal(
    pluginModule.isExecutionManagerSession("chat", {
      project_id: projectId,
      agent_id: "worker-fixed",
      session_id: "chat",
      session_key: "agent:main:chat",
      parent_id: "project-root",
      role: "worker",
      workstream_id: "single-worker",
      status: "active",
    }),
    false,
  );
});

test("stale batch, action fingerprint, and sibling responses are rejected", () => {
  const batch = {
    runId: "run-1",
    sessionId,
    turnId: "turn-1",
    batchId: "batch-1",
    reviewId: "review-1",
    epoch: 0,
    nonce: "a".repeat(48),
    expectedEventSeq: 17,
    actionIds: ["action-1"],
    actionFingerprints: ["b".repeat(64)],
  };
  const response = {
    schema_version: "1.0",
    kind: "review_response",
    project_id: projectId,
    run_id: "run-1",
    session_id: sessionId,
    turn_id: "turn-1",
    batch_id: "batch-1",
    review_id: "review-1",
    control_epoch: 0,
    next_control_epoch: 0,
    nonce: "a".repeat(48),
    next_nonce: "a".repeat(48),
    expected_event_seq: 17,
    action_ids: ["action-1"],
    action_fingerprints: ["b".repeat(64)],
    decision: "allow",
  };
  assert.ok(pluginModule.exactReviewResponse(response, batch));
  assert.equal(
    pluginModule.exactReviewResponse(
      { ...response, action_fingerprints: ["c".repeat(64)] },
      batch,
    ),
    null,
  );
  assert.equal(
    pluginModule.exactReviewResponse({ ...response, expected_event_seq: 16 }, batch),
    null,
  );
  assert.equal(
    pluginModule.exactReviewResponse(
      { ...response, session_id: siblingId },
      batch,
    ),
    null,
  );
});

test("a pause is session-local and leaves its sibling runnable", () => {
  const paused = baseControl();
  paused.pause = { active: true, reason: "targeted review", source: "test" };
  writeControl(paused);
  writeControl(baseControl(siblingId, siblingKey, "worker-2"), siblingId);
  assert.equal(pluginModule.sessionPauseActive(sessionId), true);
  assert.equal(pluginModule.sessionPauseActive(siblingId), false);
});

test("an unregistered dynamic child cannot bypass a consequential hold", async () => {
  const handlers = new Map<string, Function>();
  pluginModule.default.register({
    source: "test-unregistered",
    registerTool() {},
    on(name: string, handler: Function) {
      handlers.set(name, handler);
    },
  });
  const beforeTool = handlers.get("before_tool_call");
  assert.ok(beforeTool);
  const result = await beforeTool!(
    {
      toolName: "write",
      toolCallId: "call-unregistered",
      params: { path: "results/late/result.txt", content: "must not escape" },
    },
    { sessionId: "agent:main:subagent:not-yet-registered", runId: "run-late" },
  );
  assert.equal(result?.block, true);
  assert.match(String(result?.blockReason), /SESSION_UNREGISTERED/);
});

test("plugin holds a child mutation and releases only an exact response", async () => {
  writeControl(baseControl());
  const handlers = new Map<string, Function>();
  const tools = new Map<string, Record<string, unknown>>();
  const api = {
    source: "test",
    registerTool(tool: Record<string, unknown>) {
      tools.set(String(tool.name), tool);
    },
    on(name: string, handler: Function) {
      handlers.set(name, handler);
    },
  };
  pluginModule.default.register(api);
  assert.ok(tools.has("jarvis_control"));
  assert.ok(fs.existsSync(readyPath));
  const beforeTool = handlers.get("before_tool_call");
  assert.ok(beforeTool);
  const held = beforeTool!(
    {
      toolName: "write",
      toolCallId: "call-1",
      params: { path: "results/analysis/result.txt", content: "bounded" },
    },
    { sessionId, runId: "run-1" },
  );
  const review = await waitForReviewForTool("call-1");
  assert.equal(review.payload.session_id, sessionId);
  assert.equal(review.payload.raw_trace_included, undefined);
  assert.ok(review.payload.expected_event_seq > 0);
  assert.equal(review.payload.held_actions.length, 1);

  const control = baseControl();
  control.review_responses = [
    {
      schema_version: "1.0",
      kind: "review_response",
      control_id: "control-1",
      project_id: projectId,
      run_id: "run-1",
      session_id: sessionId,
      turn_id: review.payload.turn_id,
      review_id: review.payload.review_id,
      batch_id: review.payload.batch_id,
      action_ids: review.payload.held_actions.map((item: any) => item.action_id),
      action_fingerprints: review.payload.held_actions.map(
        (item: any) => item.action_fingerprint,
      ),
      control_epoch: 0,
      next_control_epoch: 0,
      nonce: "a".repeat(48),
      next_nonce: "a".repeat(48),
      expected_event_seq: review.payload.expected_event_seq,
      decision: "allow",
      decision_id: "",
      guidance: "",
      guidance_sha256: "",
      created_at: new Date().toISOString(),
    },
  ];
  writeControl(control);
  assert.equal(await held, undefined);

  const events = fs
    .readFileSync(eventPath, "utf8")
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
  assert.ok(events.some((event) => event.type === "jarvis.review.allowed"));
  for (const event of events) {
    assertProjectEventEnvelope(event);
    for (const field of [
      "project_id",
      "agent_id",
      "session_id",
      "parent_id",
      "role",
      "turn_id",
      "batch_id",
      "action_id",
    ]) {
      assert.equal(typeof event.payload[field], "string");
      assert.ok(event.payload[field].length > 0);
    }
  }
});

test("exact tool continuation ack closes guidance delivery and application", async () => {
  writeControl(baseControl());
  const handlers = new Map<string, Function>();
  const tools = new Map<string, Record<string, any>>();
  pluginModule.default.register({
    source: "test-interrupt",
    registerTool(tool: Record<string, any>) {
      tools.set(String(tool.name), tool);
    },
    on(name: string, handler: Function) {
      handlers.set(name, handler);
    },
  });
  const beforeTool = handlers.get("before_tool_call");
  const persist = handlers.get("tool_result_persist");
  const beforePrompt = handlers.get("before_prompt_build");
  assert.ok(beforeTool && persist && beforePrompt);

  const heldPromise = beforeTool!(
    {
      toolName: "write",
      toolCallId: "call-interrupt",
      params: { path: "results/analysis/revised.txt", content: "old choice" },
    },
    { sessionId, runId: "run-interrupt" },
  );
  let review: Record<string, any> | undefined;
  const deadline = Date.now() + 2000;
  while (Date.now() < deadline && !review) {
    const events = fs.existsSync(eventPath)
      ? fs
          .readFileSync(eventPath, "utf8")
          .split("\n")
          .filter(Boolean)
          .map((line) => JSON.parse(line))
      : [];
    review = events
      .filter((event) => event.type === "jarvis.review.requested")
      .find((event) =>
        event.payload.held_actions.some(
          (item: any) => item.tool_call_id === "call-interrupt",
        ),
      );
    if (!review) await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.ok(review);
  const nextNonce = "b".repeat(48);
  const guidance = "Requester answer for this decision: use option B.";
  const guidanceSha256 = crypto.createHash("sha256").update(guidance).digest("hex");
  const deliveryReceiptId = `delivery-${"c".repeat(24)}`;
  const response = {
    schema_version: "1.0",
    kind: "review_response",
    control_id: "control-interrupt",
    project_id: projectId,
    run_id: "run-interrupt",
    session_id: sessionId,
    turn_id: review!.payload.turn_id,
    review_id: review!.payload.review_id,
    batch_id: review!.payload.batch_id,
    action_ids: review!.payload.held_actions.map((item: any) => item.action_id),
    action_fingerprints: review!.payload.held_actions.map(
      (item: any) => item.action_fingerprint,
    ),
    control_epoch: 0,
    next_control_epoch: 1,
    nonce: "a".repeat(48),
    next_nonce: nextNonce,
    expected_event_seq: review!.payload.expected_event_seq,
    decision: "interrupt_replan",
    decision_id: "decision-interrupt",
    guidance,
    guidance_sha256: guidanceSha256,
    delivery_receipt_id: deliveryReceiptId,
    created_at: new Date().toISOString(),
  };
  const state = baseControl();
  state.control_epoch = 1;
  state.nonce = nextNonce;
  state.review_responses = [response];
  state.delivery_receipts = [
    {
      receipt_id: deliveryReceiptId,
      decision_id: "decision-interrupt",
      target_session_id: sessionId,
      control_epoch: 1,
      nonce: nextNonce,
      guidance_sha256: guidanceSha256,
      status: "delivered",
    },
  ];
  state.guidance_queue = [
    {
      receipt_id: deliveryReceiptId,
      decision_id: "decision-interrupt",
      text: guidance,
      guidance_sha256: guidanceSha256,
      control_epoch: 1,
      nonce: nextNonce,
      route: "next_model_boundary",
      scope: "worker",
    },
  ];
  writeControl(state);
  const held = await heldPromise;
  assert.equal((held as any).block, true);
  assert.match(String((held as any).blockReason), /JARVIS_HELD_ACTION_INVALIDATED_V1/);

  await persist!(
    {
      toolCallId: "call-interrupt",
      message: { content: (held as any).blockReason },
    },
    { sessionId, runId: "run-interrupt" },
  );
  await beforeTool!(
    { toolName: "jarvis_control", toolCallId: "call-ack", params: {} },
    { sessionId, runId: "run-interrupt" },
  );
  const controlTool = tools.get("jarvis_control");
  assert.ok(controlTool);
  const ack = await controlTool!.execute("call-ack", {
    judgment_id: "decision-interrupt",
    session_id: sessionId,
    control_epoch: 1,
    ack_nonce: nextNonce,
    action_fingerprint: crypto
      .createHash("sha256")
      .update(review!.payload.held_actions[0].action_fingerprint)
      .digest("hex"),
  });
  assert.equal(ack.details.status, "acknowledged");

  const events = fs
    .readFileSync(eventPath, "utf8")
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
  const delivered = events.filter(
    (event) =>
      event.type === "control.guidance.delivered" &&
      event.payload.delivery_receipt_id === deliveryReceiptId,
  );
  const applied = events.filter(
    (event) =>
      event.type === "control.guidance.applied" &&
      event.payload.delivery_receipt_id === deliveryReceiptId,
  );
  assert.equal(delivered.length, 1);
  assert.equal(applied.length, 1);
  assert.equal(applied[0].payload.application_basis, "exact_tool_result_continuation_ack");
  assert.equal(
    await beforePrompt!({}, { sessionId, runId: "run-interrupt" }),
    undefined,
  );
});
