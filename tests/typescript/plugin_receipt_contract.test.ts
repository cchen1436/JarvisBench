import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test, { after } from "node:test";

type JsonRecord = Record<string, any>;

const root = fs.mkdtempSync(path.join(os.tmpdir(), "jarvisbench-receipts-"));
const controlRoot = path.join(root, "control");
const registryPath = path.join(controlRoot, "registry.json");
const eventPath = path.join(root, "events", "project_events.jsonl");
const projectId = "receipt-project";
const targetId = "child-target";
const targetKey = "agent:main:subagent:child-target";
const negativeId = "child-negative";
const negativeKey = "agent:main:subagent:child-negative";
const siblingId = "child-sibling";
const siblingKey = "agent:main:subagent:child-sibling";
const batchId = "child-batch";
const batchKey = "agent:main:subagent:child-batch";
// A single-agent OpenClaw session can use the conventional runtime id which
// would otherwise look like the MAS Parent.  The registry role is authoritative.
const fixedSingleId = "chat";
const fixedSingleKey = "agent:main:chat";

process.env.JARVIS_MAS_PROJECT_ID = projectId;
process.env.JARVIS_MAS_CONTROL_ROOT = controlRoot;
process.env.JARVIS_MAS_REGISTRY_JSON = registryPath;
process.env.JARVIS_HOOK_EVENTS_JSONL = eventPath;
process.env.JARVIS_MAS_PARENT_RUNTIME_SESSION_ID = fixedSingleId;
process.env.JARVIS_MAS_PARENT_SESSION_KEY = fixedSingleKey;
process.env.JARVIS_AUTONOMOUS_REVIEW = "1";
process.env.JARVIS_MAS_DYNAMIC_REQUIRED = "1";
process.env.JARVIS_MAS_PLUGIN_ROLE = "agent";
process.env.JARVIS_BATCH_SETTLE_MS = "5";
process.env.JARVIS_REVIEW_POLL_MS = "25";
process.env.JARVIS_REVIEW_TIMEOUT_MS = "2000";
process.env.JARVIS_REGISTRATION_WAIT_MS = "50";

function binding(
  sessionId: string,
  sessionKey: string,
  agentId: string,
): JsonRecord {
  return {
    project_id: projectId,
    agent_id: agentId,
    session_id: sessionId,
    session_key: sessionKey,
    parent_id: "project-root",
    role: "worker",
    workstream_id: agentId,
    status: "active",
  };
}

const identities = new Map<string, JsonRecord>([
  [targetId, binding(targetId, targetKey, "worker-target")],
  [negativeId, binding(negativeId, negativeKey, "worker-negative")],
  [siblingId, binding(siblingId, siblingKey, "worker-sibling")],
  [batchId, binding(batchId, batchKey, "worker-batch")],
  [fixedSingleId, binding(fixedSingleId, fixedSingleKey, "worker-0")],
]);

fs.mkdirSync(controlRoot, { recursive: true, mode: 0o700 });
fs.writeFileSync(
  registryPath,
  `${JSON.stringify({
    schema_version: "1.0",
    kind: "dynamic_child_registry",
    project_id: projectId,
    parent_session_id: "no-parent",
    parent_session_key: "no-parent",
    sessions: Object.fromEntries(identities),
    aliases: {
      [targetId]: targetId,
      [targetKey]: targetId,
      [negativeId]: negativeId,
      [negativeKey]: negativeId,
      [siblingId]: siblingId,
      [siblingKey]: siblingId,
      [batchId]: batchId,
      [batchKey]: batchId,
      [fixedSingleId]: fixedSingleId,
      [fixedSingleKey]: fixedSingleId,
    },
  })}\n`,
  { mode: 0o600 },
);

const pluginModule = await import(
  "../../plugins/openclaw/jarvis_supervisor/index.ts"
);

const handlers = new Map<string, Function>();
const tools = new Map<string, JsonRecord>();
pluginModule.default.register({
  source: "receipt-contract-test",
  registerTool(tool: JsonRecord) {
    tools.set(String(tool.name), tool);
  },
  on(name: string, handler: Function) {
    handlers.set(name, handler);
  },
});

after(() => {
  fs.rmSync(root, { recursive: true, force: true });
});

function identityFor(sessionId: string): JsonRecord {
  const identity = identities.get(sessionId);
  assert.ok(identity, `missing test identity ${sessionId}`);
  return identity;
}

function baseControl(sessionId: string): JsonRecord {
  const identity = identityFor(sessionId);
  return {
    schema_version: "1.0",
    kind: "dynamic_session_control",
    protocol_version: "1.0-release",
    revision: 1,
    project_id: projectId,
    agent_id: identity.agent_id,
    session_id: identity.session_id,
    session_key: identity.session_key,
    parent_id: identity.parent_id,
    role: identity.role,
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

function controlPath(sessionId: string): string {
  return path.join(
    controlRoot,
    "sessions",
    pluginModule.masSessionNamespace(sessionId),
    "control.json",
  );
}

function writeControl(value: JsonRecord): void {
  const target = controlPath(String(value.session_id));
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  const temporary = `${target}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value)}\n`, { mode: 0o600 });
  fs.renameSync(temporary, target);
}

for (const sessionId of identities.keys()) writeControl(baseControl(sessionId));

function events(): JsonRecord[] {
  if (!fs.existsSync(eventPath)) return [];
  return fs
    .readFileSync(eventPath, "utf8")
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

async function waitForReview(
  toolCallId: string,
  timeoutMs = 1200,
): Promise<JsonRecord> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const review = events().find(
      (event) =>
        event.type === "jarvis.review.requested" &&
        event.payload.held_actions?.some(
          (action: JsonRecord) => action.tool_call_id === toolCallId,
        ),
    );
    if (review) return review;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`review did not arrive for ${toolCallId}`);
}

function sha256(value: string): string {
  return crypto.createHash("sha256").update(value, "utf8").digest("hex");
}

function interruptResponse(
  review: JsonRecord,
  sessionId: string,
  receiptId: string,
): JsonRecord {
  const guidance = "Use the narrowly scoped requester answer for this held action.";
  return {
    schema_version: "1.0",
    kind: "review_response",
    control_id: `control-${review.payload.review_id}`,
    project_id: projectId,
    run_id: review.payload.run_id,
    session_id: sessionId,
    turn_id: review.payload.turn_id,
    review_id: review.payload.review_id,
    batch_id: review.payload.batch_id,
    action_ids: review.payload.held_actions.map(
      (item: JsonRecord) => item.action_id,
    ),
    action_fingerprints: review.payload.held_actions.map(
      (item: JsonRecord) => item.action_fingerprint,
    ),
    control_epoch: 0,
    next_control_epoch: 1,
    nonce: "a".repeat(48),
    next_nonce: "b".repeat(48),
    expected_event_seq: review.payload.expected_event_seq,
    decision: "interrupt_replan",
    decision_id: `decision-${sessionId}`,
    guidance,
    guidance_sha256: sha256(guidance),
    delivery_receipt_id: receiptId,
    created_at: new Date().toISOString(),
  };
}

function allowResponse(review: JsonRecord, sessionId: string): JsonRecord {
  return {
    schema_version: "1.0",
    kind: "review_response",
    control_id: `control-${review.payload.review_id}`,
    project_id: projectId,
    run_id: review.payload.run_id,
    session_id: sessionId,
    turn_id: review.payload.turn_id,
    review_id: review.payload.review_id,
    batch_id: review.payload.batch_id,
    action_ids: review.payload.held_actions.map(
      (item: JsonRecord) => item.action_id,
    ),
    action_fingerprints: review.payload.held_actions.map(
      (item: JsonRecord) => item.action_fingerprint,
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
  };
}

function exactDelivery(response: JsonRecord, sessionId: string): JsonRecord {
  return {
    receipt_id: response.delivery_receipt_id,
    decision_id: response.decision_id,
    target_session_id: sessionId,
    control_epoch: response.next_control_epoch,
    nonce: response.next_nonce,
    guidance_sha256: response.guidance_sha256,
  };
}

async function proposeMutation(
  sessionId: string,
  toolCallId: string,
): Promise<unknown> {
  const beforeTool = handlers.get("before_tool_call");
  assert.ok(beforeTool);
  return beforeTool(
    {
      toolName: "write",
      toolCallId,
      params: {
        path: `results/${sessionId}/result.txt`,
        content: "bounded mutation",
      },
    },
    { sessionId, runId: `run-${sessionId}` },
  );
}

async function executeAck(
  sessionId: string,
  toolCallId: string,
  params: JsonRecord,
): Promise<JsonRecord> {
  const beforeTool = handlers.get("before_tool_call");
  const controlTool = tools.get("jarvis_control");
  assert.ok(beforeTool);
  assert.ok(controlTool);
  await beforeTool(
    { toolName: "jarvis_control", toolCallId, params },
    { sessionId, runId: `run-${sessionId}` },
  );
  return controlTool.execute(toolCallId, params);
}

test("interrupt responses require exact generation and receipt identity", () => {
  const batch = {
    runId: "run-contract",
    sessionId: targetId,
    turnId: "turn-1",
    batchId: "batch-1",
    reviewId: "review-1",
    epoch: 0,
    nonce: "a".repeat(48),
    expectedEventSeq: 41,
    actionIds: ["action-1"],
    actionFingerprints: ["c".repeat(64)],
  };
  const review = {
    payload: {
      run_id: batch.runId,
      turn_id: batch.turnId,
      batch_id: batch.batchId,
      review_id: batch.reviewId,
      expected_event_seq: batch.expectedEventSeq,
      held_actions: [
        {
          action_id: batch.actionIds[0],
          action_fingerprint: batch.actionFingerprints[0],
        },
      ],
    },
  };
  const response = interruptResponse(
    review,
    targetId,
    `delivery-${"d".repeat(24)}`,
  );
  assert.ok(pluginModule.exactReviewResponse(response, batch));
  assert.equal(
    pluginModule.exactReviewResponse(
      { ...response, delivery_receipt_id: "delivery-not-exact" },
      batch,
    ),
    null,
  );
  assert.equal(
    pluginModule.exactReviewResponse(
      { ...response, next_control_epoch: 0 },
      batch,
    ),
    null,
  );
  assert.equal(
    pluginModule.exactReviewResponse(
      { ...response, nonce: "e".repeat(48) },
      batch,
    ),
    null,
  );
  assert.equal(
    pluginModule.exactReviewResponse(
      { ...response, action_fingerprints: ["f".repeat(64)] },
      batch,
    ),
    null,
  );
});

test("a stale delivery receipt fails closed without touching a sibling", async () => {
  const siblingBefore = fs.readFileSync(controlPath(siblingId), "utf8");
  const held = proposeMutation(negativeId, "call-stale-receipt");
  const review = await waitForReview("call-stale-receipt");
  const response = interruptResponse(
    review,
    negativeId,
    `delivery-${"e".repeat(24)}`,
  );
  const state = baseControl(negativeId);
  state.control_epoch = 1;
  state.nonce = response.next_nonce;
  state.review_responses = [response];
  state.delivery_receipts = [
    {
      ...exactDelivery(response, negativeId),
      // This receipt belongs to the prior generation and must not authorize
      // delivery even though every other field is exact.
      control_epoch: 0,
    },
    {
      ...exactDelivery(response, negativeId),
      // A current receipt for another delivery is equally insufficient.
      receipt_id: `delivery-${"1".repeat(24)}`,
    },
  ];
  writeControl(state);

  const result = (await held) as JsonRecord;
  assert.equal(result.block, true);
  assert.match(String(result.blockReason), /exact delivery receipt/i);
  const failure = events().find(
    (event) =>
      event.type === "jarvis.review.failed_closed" &&
      event.payload.review_id === review.payload.review_id,
  );
  assert.equal(failure?.payload.reason, "delivery_receipt_missing_or_stale");
  assert.equal(fs.readFileSync(controlPath(siblingId), "utf8"), siblingBefore);
  assert.equal(
    events().some(
      (event) =>
        event.type === "control.guidance.delivered" &&
        event.payload.session_id === negativeId,
    ),
    false,
  );
});

test("persisted exact guidance is delivered and only an exact ack applies it", async () => {
  const siblingBefore = fs.readFileSync(controlPath(siblingId), "utf8");
  const held = proposeMutation(targetId, "call-exact-delivery");
  const review = await waitForReview("call-exact-delivery");
  const response = interruptResponse(
    review,
    targetId,
    `delivery-${"f".repeat(24)}`,
  );
  const state = baseControl(targetId);
  state.control_epoch = 1;
  state.nonce = response.next_nonce;
  state.review_responses = [response];
  state.delivery_receipts = [exactDelivery(response, targetId)];
  writeControl(state);

  const result = (await held) as JsonRecord;
  assert.equal(result.block, true);
  const envelope = String(result.blockReason);
  const envelopeRecord = JSON.parse(envelope);
  assert.equal(envelopeRecord.judgment_id, response.decision_id);
  assert.equal(envelopeRecord.session_id, targetId);

  const persist = handlers.get("tool_result_persist");
  assert.ok(persist);
  await persist(
    { toolCallId: "call-exact-delivery", message: "unrelated tool result" },
    { sessionId: targetId, runId: `run-${targetId}` },
  );
  assert.ok(
    events().some(
      (event) =>
        event.type === "control.interrupt.delivery_rejected" &&
        event.payload.tool_call_id === "call-exact-delivery",
    ),
  );
  assert.equal(
    events().some(
      (event) =>
        event.type === "control.guidance.delivered" &&
        event.payload.delivery_receipt_id === response.delivery_receipt_id,
    ),
    false,
  );

  await persist(
    {
      toolCallId: "call-exact-delivery",
      message: { role: "tool", content: envelope },
    },
    { sessionId: targetId, runId: `run-${targetId}` },
  );
  const delivered = events().filter(
    (event) =>
      event.type === "control.guidance.delivered" &&
      event.payload.delivery_receipt_id === response.delivery_receipt_id,
  );
  assert.equal(delivered.length, 1);
  assert.equal(delivered[0].payload.decision_id, response.decision_id);
  assert.equal(delivered[0].payload.control_epoch, 1);
  assert.equal(delivered[0].payload.nonce, response.next_nonce);
  assert.equal(delivered[0].payload.guidance_sha256, response.guidance_sha256);
  assert.equal(delivered[0].payload.exact, true);

  const exactAck = {
    judgment_id: envelopeRecord.judgment_id,
    session_id: envelopeRecord.session_id,
    control_epoch: envelopeRecord.control_epoch,
    ack_nonce: envelopeRecord.ack_nonce,
    action_fingerprint: envelopeRecord.batch_action_fingerprint,
  };
  const wrongNonce = await executeAck(targetId, "ack-wrong-nonce", {
    ...exactAck,
    ack_nonce: "9".repeat(48),
  });
  assert.equal(wrongNonce.details.status, "rejected");
  const wrongFingerprint = await executeAck(targetId, "ack-wrong-fingerprint", {
    ...exactAck,
    action_fingerprint: "8".repeat(64),
  });
  assert.equal(wrongFingerprint.details.status, "rejected");
  assert.equal(
    events().some(
      (event) =>
        event.type === "control.guidance.applied" &&
        event.payload.delivery_receipt_id === response.delivery_receipt_id,
    ),
    false,
  );

  const beforeTool = handlers.get("before_tool_call");
  assert.ok(beforeTool);
  const siblingRead = await beforeTool(
    { toolName: "read", toolCallId: "sibling-read", params: { path: "public/a" } },
    { sessionId: siblingId, runId: `run-${siblingId}` },
  );
  assert.equal(siblingRead, undefined);
  assert.equal(fs.readFileSync(controlPath(siblingId), "utf8"), siblingBefore);

  const accepted = await executeAck(targetId, "ack-exact", exactAck);
  assert.equal(accepted.details.status, "acknowledged");
  const applications = events().filter(
    (event) =>
      event.type === "control.guidance.applied" &&
      event.payload.delivery_receipt_id === response.delivery_receipt_id,
  );
  assert.equal(applications.length, 1);
  assert.equal(applications[0].payload.decision_id, response.decision_id);
  assert.equal(applications[0].payload.control_epoch, 1);
  assert.equal(applications[0].payload.nonce, response.next_nonce);
  assert.equal(applications[0].payload.guidance_sha256, response.guidance_sha256);
  assert.equal(
    applications[0].payload.application_basis,
    "exact_tool_result_continuation_ack",
  );
});

test("one multi-action batch persists full guidance once and emits one delivery receipt", async () => {
  const first = proposeMutation(batchId, "call-batch-a");
  const second = proposeMutation(batchId, "call-batch-b");
  const review = await waitForReview("call-batch-a");
  assert.equal(review.payload.held_actions.length, 2);
  const response = interruptResponse(
    review,
    batchId,
    `delivery-${"d".repeat(24)}`,
  );
  const state = baseControl(batchId);
  state.control_epoch = 1;
  state.nonce = response.next_nonce;
  state.review_responses = [response];
  state.delivery_receipts = [exactDelivery(response, batchId)];
  writeControl(state);

  const results = (await Promise.all([first, second])) as JsonRecord[];
  const full = results.filter((item) => {
    const value = JSON.parse(String(item.blockReason));
    return value.type === "JARVIS_HELD_ACTION_INVALIDATED_V1";
  });
  const markers = results.filter((item) => {
    const value = JSON.parse(String(item.blockReason));
    return value.kind === "jarvis_batch_sibling_invalidated";
  });
  assert.equal(full.length, 1);
  assert.equal(markers.length, 1);

  const persist = handlers.get("tool_result_persist");
  assert.ok(persist);
  for (const [toolCallId, item] of [
    ["call-batch-a", results[0]],
    ["call-batch-b", results[1]],
  ] as const) {
    await persist(
      { toolCallId, message: { role: "tool", content: item.blockReason } },
      { sessionId: batchId, runId: `run-${batchId}` },
    );
    const deliveries = events().filter(
      (event) =>
        event.type === "control.guidance.delivered" &&
        event.payload.delivery_receipt_id === response.delivery_receipt_id,
    );
    assert.equal(deliveries.length, toolCallId === "call-batch-a" ? 0 : 1);
  }
  const delivery = events().find(
    (event) =>
      event.type === "control.guidance.delivered" &&
      event.payload.delivery_receipt_id === response.delivery_receipt_id,
  );
  assert.deepEqual(delivery?.payload.tool_call_ids, ["call-batch-a", "call-batch-b"]);
});

test("a registered fixed single worker cannot be bypassed as the Parent", async () => {
  const held = proposeMutation(fixedSingleId, "call-fixed-single");
  const review = await waitForReview("call-fixed-single", 800);
  assert.equal(review.payload.session_id, fixedSingleId);
  assert.equal(review.payload.role, "worker");

  const state = baseControl(fixedSingleId);
  state.review_responses = [allowResponse(review, fixedSingleId)];
  writeControl(state);
  assert.equal(await held, undefined);
  assert.ok(
    events().some(
      (event) =>
        event.type === "jarvis.review.allowed" &&
        event.payload.review_id === review.payload.review_id,
    ),
  );
});

test("batch finalization faults resolve held actions fail-closed", async () => {
  const held = proposeMutation(siblingId, "call-finalization-fault");
  await new Promise((resolve) => setTimeout(resolve, 1));
  fs.chmodSync(eventPath, 0o400);
  const result = (await Promise.race([
    held,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error("held action remained unresolved")), 800),
    ),
  ])) as JsonRecord;
  assert.equal(result.block, true);
  assert.match(String(result.blockReason), /finalization failed/i);
  // The best-effort failure event cannot be written while the injected event
  // sink fault is active; restoring it proves no process-level rejection was
  // required to resolve the held action.
  fs.chmodSync(eventPath, 0o600);
});
