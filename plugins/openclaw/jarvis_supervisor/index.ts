import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { classifyReadOnlyExec } from "./read_only_exec.ts";

type JsonRecord = Record<string, unknown>;

export const PLUGIN_ID = "jarvisbench-mas-supervisor";
export const PLUGIN_VERSION = "0.1.0";
export const CONTROL_PROTOCOL_VERSION = "1.0-release";

const PROJECT_ID = String(process.env.JARVIS_MAS_PROJECT_ID || "");
const CONTROL_ROOT = String(process.env.JARVIS_MAS_CONTROL_ROOT || "");
const REGISTRY_PATH = String(process.env.JARVIS_MAS_REGISTRY_JSON || "");
const EVENT_PATH = String(process.env.JARVIS_HOOK_EVENTS_JSONL || "");
const PLUGIN_READY_PATH = String(process.env.JARVIS_MAS_PLUGIN_READY_JSON || "");
const PARENT_SESSION_ID = String(
  process.env.JARVIS_MAS_PARENT_RUNTIME_SESSION_ID || "chat",
);
const PARENT_SESSION_KEY = String(
  process.env.JARVIS_MAS_PARENT_SESSION_KEY || "agent:main:chat",
);
const WORKSPACE_ROOT = path.resolve(
  String(process.env.JARVIS_WORKSPACE_ROOT || "/workspace"),
);
const AUTONOMOUS_REVIEW = process.env.JARVIS_AUTONOMOUS_REVIEW === "1";
const PLUGIN_ROLE = String(process.env.JARVIS_MAS_PLUGIN_ROLE || "agent");
const DYNAMIC_REQUIRED = process.env.JARVIS_MAS_DYNAMIC_REQUIRED === "1";
const REVIEW_TIMEOUT_MS = Math.max(
  1_000,
  Number(process.env.JARVIS_REVIEW_TIMEOUT_MS || 420_000),
);
const REVIEW_POLL_MS = Math.max(
  25,
  Number(process.env.JARVIS_REVIEW_POLL_MS || 100),
);
const REGISTRATION_WAIT_MS = Math.max(
  25,
  Math.min(
    10_000,
    Number(process.env.JARVIS_REGISTRATION_WAIT_MS || 10_000),
  ),
);
const BATCH_SETTLE_MS = Math.max(
  0,
  Number(process.env.JARVIS_BATCH_SETTLE_MS || 50),
);
const MAX_EVENT_LINE_BYTES = 64 * 1024;
const MAX_PREVIEW_CHARS = 900;

const turnBySession = new Map<string, number>();
const batches = new Map<string, ReviewBatch>();
const controlToolSessions = new Map<string, string>();
const pendingAcks = new Map<string, PendingAck>();
const pendingDeliveries = new Map<string, PendingDelivery>();
const injectedBySession = new Map<string, Injection>();
const appliedReceiptIds = new Set<string>();

export type SessionIdentity = {
  project_id: string;
  agent_id: string;
  session_id: string;
  session_key: string;
  parent_id: string;
  role: "parent" | "worker";
  workstream_id: string;
  status: string;
};

type HeldAction = {
  actionId: string;
  toolCallId: string;
  toolName: string;
  paramsSha256: string;
  actionFingerprint: string;
  paramsPreview: string;
  artifactPaths: string[];
  resolve: (value: unknown) => void;
};

type ReviewBatch = {
  runId: string;
  sessionId: string;
  turnId: string;
  batchId: string;
  reviewId: string;
  epoch: number;
  nonce: string;
  expectedEventSeq: number;
  actions: HeldAction[];
  state: "collecting" | "waiting" | "resolved";
};

type PendingAck = {
  sessionId: string;
  judgmentId: string;
  controlEpoch: number;
  ackNonce: string;
  batchActionFingerprint: string;
  actionFingerprints: string[];
  deliveryReceiptId: string;
  guidanceSha256: string;
  envelopeSha256: string;
  expectedDeliveries: number;
  deliveredToolCallIds: Set<string>;
  deliveryEventEmitted: boolean;
  acknowledged: boolean;
};

type PendingDelivery = {
  sessionId: string;
  toolCallId: string;
  actionId: string;
  envelope: string;
  envelopeSha256: string;
  decisionId: string;
  deliveryReceiptId: string;
  controlEpoch: number;
  nonce: string;
  guidanceSha256: string;
};

type Injection = {
  envelope: string;
  receipts: JsonRecord[];
  modelBoundaryId: string;
};

function asRecord(value: unknown): JsonRecord | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const record = value as JsonRecord;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableStringify(record[key])}`)
    .join(",")}}`;
}

function sha256(value: string): string {
  return crypto.createHash("sha256").update(value, "utf8").digest("hex");
}

function compact(value: unknown, maximum = MAX_PREVIEW_CHARS): string {
  const text = typeof value === "string" ? value : stableStringify(value);
  return text.replace(/\s+/g, " ").trim().slice(0, maximum);
}

function collectStrings(value: unknown, output: string[] = []): string[] {
  if (typeof value === "string") output.push(value);
  else if (Array.isArray(value)) value.forEach((item) => collectStrings(item, output));
  else if (value && typeof value === "object") {
    Object.values(value as JsonRecord).forEach((item) => collectStrings(item, output));
  }
  return output;
}

function sessionKey(event: JsonRecord, ctx: JsonRecord): string {
  return String(
    ctx.sessionId ||
      ctx.sessionKey ||
      event.sessionId ||
      event.sessionKey ||
      PARENT_SESSION_ID,
  );
}

function runKey(event: JsonRecord, ctx: JsonRecord): string {
  return String(ctx.runId || event.runId || `run-${process.pid}`);
}

function isParentSession(sessionIdOrKey: string): boolean {
  return sessionIdOrKey === PARENT_SESSION_ID || sessionIdOrKey === PARENT_SESSION_KEY;
}

export function isExecutionManagerSession(
  sessionIdOrKey: string,
  identity: SessionIdentity | null = resolveMasSessionIdentity(sessionIdOrKey),
): boolean {
  // A registered role is authoritative. This keeps the MAS Parent exempt while
  // allowing a fixed single worker to use an otherwise conventional session id
  // such as "chat". The raw sentinel is only a bootstrap fallback for an
  // unregistered execution-manager process.
  return identity ? identity.role === "parent" : isParentSession(sessionIdOrKey);
}

function readJson(target: string): JsonRecord | null {
  try {
    const stat = fs.lstatSync(target);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 512 * 1024) return null;
    return asRecord(JSON.parse(fs.readFileSync(target, "utf8")));
  } catch {
    return null;
  }
}

function registry(): JsonRecord {
  return (REGISTRY_PATH && readJson(REGISTRY_PATH)) || {};
}

export function resolveMasSessionIdentity(sessionIdOrKey: string): SessionIdentity | null {
  const root = registry();
  const sessions = asRecord(root.sessions);
  const aliases = asRecord(root.aliases);
  if (!sessions) return null;
  const canonical = String(aliases?.[sessionIdOrKey] || sessionIdOrKey);
  const raw = asRecord(sessions[canonical]);
  if (!raw) return null;
  const role = raw.role === "parent" ? "parent" : raw.role === "worker" ? "worker" : null;
  if (!role) return null;
  const identity: SessionIdentity = {
    project_id: String(raw.project_id || ""),
    agent_id: String(raw.agent_id || ""),
    session_id: String(raw.session_id || ""),
    session_key: String(raw.session_key || ""),
    parent_id: String(raw.parent_id || ""),
    role,
    workstream_id: String(raw.workstream_id || ""),
    status: String(raw.status || ""),
  };
  return identity.project_id === PROJECT_ID ? identity : null;
}

export function masSessionNamespace(sessionId: string): string {
  return sha256(sessionId);
}

function controlPath(sessionId: string): string {
  return path.join(CONTROL_ROOT, "sessions", masSessionNamespace(sessionId), "control.json");
}

export function validateSessionControl(
  value: unknown,
  sessionId: string,
  identity: SessionIdentity | null = resolveMasSessionIdentity(sessionId),
): value is JsonRecord {
  const state = asRecord(value);
  return Boolean(
    state &&
      identity &&
      state.schema_version === "1.0" &&
      state.kind === "dynamic_session_control" &&
      state.protocol_version === CONTROL_PROTOCOL_VERSION &&
      state.project_id === PROJECT_ID &&
      state.agent_id === identity.agent_id &&
      state.session_id === identity.session_id &&
      state.session_key === identity.session_key &&
      state.parent_id === identity.parent_id &&
      state.role === identity.role &&
      Number.isInteger(state.control_epoch) &&
      Number(state.control_epoch) >= 0 &&
      typeof state.nonce === "string" &&
      /^[a-f0-9]{32,128}$/.test(state.nonce),
  );
}

function readControl(sessionId: string): JsonRecord {
  const value = CONTROL_ROOT ? readJson(controlPath(sessionId)) : null;
  return validateSessionControl(value, sessionId) ? value : {};
}

export function sessionPauseActive(sessionId: string): boolean {
  const pause = asRecord(readControl(sessionId).pause);
  return pause?.active === true;
}

function eventIdentity(sessionId: string): SessionIdentity | null {
  const known = resolveMasSessionIdentity(sessionId);
  if (known) return known;
  if (sessionId === PARENT_SESSION_ID || sessionId === PARENT_SESSION_KEY) {
    return {
      project_id: PROJECT_ID || "unconfigured-project",
      agent_id: "parent",
      session_id: PARENT_SESSION_ID,
      session_key: PARENT_SESSION_KEY,
      parent_id: "parent",
      role: "parent",
      workstream_id: "",
      status: "active",
    };
  }
  return null;
}

let localEventSequence = 0;
function appendEvent(type: string, sessionId: string, payload: JsonRecord = {}): number {
  if (!EVENT_PATH) return 0;
  const identity = eventIdentity(sessionId);
  if (!identity) return 0;
  const next = Date.now() * 1000 + (localEventSequence++ % 1000);
  const enriched: JsonRecord = {
    project_id: identity.project_id,
    agent_id: identity.agent_id,
    session_id: identity.session_id,
    parent_id: identity.parent_id,
    role: identity.role,
    turn_id: String(payload.turn_id || `turn-${turnBySession.get(identity.session_id) || 0}`),
    batch_id: String(payload.batch_id || "batch-none"),
    action_id: String(payload.action_id || "action-none"),
    ...payload,
  };
  if (type === "jarvis.review.requested") {
    enriched.expected_event_seq = next;
  }
  const record = {
    seq: next,
    event_id: `event-${crypto.randomUUID()}`,
    ts: new Date().toISOString(),
    type,
    payload: enriched,
  };
  const line = `${JSON.stringify(record)}\n`;
  if (Buffer.byteLength(line, "utf8") > MAX_EVENT_LINE_BYTES) {
    throw new Error("bounded Jarvis project event exceeded 64 KiB");
  }
  fs.mkdirSync(path.dirname(EVENT_PATH), { recursive: true, mode: 0o700 });
  const fd = fs.openSync(EVENT_PATH, "a", 0o600);
  try {
    fs.writeSync(fd, line, undefined, "utf8");
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  return next;
}

function writePrivateJson(target: string, value: unknown): void {
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  const temporary = `${target}.tmp.${process.pid}.${crypto.randomBytes(4).toString("hex")}`;
  fs.writeFileSync(temporary, `${JSON.stringify(value)}\n`, { mode: 0o600 });
  fs.renameSync(temporary, target);
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function sameStrings(value: unknown, expected: string[]): boolean {
  return (
    Array.isArray(value) &&
    value.length === expected.length &&
    value.every((item, index) => item === expected[index])
  );
}

export function exactReviewResponse(
  response: unknown,
  batch: Omit<ReviewBatch, "actions" | "state"> & {
    actionIds: string[];
    actionFingerprints: string[];
  },
): JsonRecord | null {
  const value = asRecord(response);
  if (
    !value ||
    value.schema_version !== "1.0" ||
    value.kind !== "review_response" ||
    value.project_id !== PROJECT_ID ||
    value.run_id !== batch.runId ||
    value.session_id !== batch.sessionId ||
    value.turn_id !== batch.turnId ||
    value.batch_id !== batch.batchId ||
    value.review_id !== batch.reviewId ||
    value.control_epoch !== batch.epoch ||
    value.nonce !== batch.nonce ||
    value.expected_event_seq !== batch.expectedEventSeq ||
    !sameStrings(value.action_ids, batch.actionIds) ||
    !sameStrings(value.action_fingerprints, batch.actionFingerprints) ||
    !["allow", "interrupt_replan"].includes(String(value.decision || ""))
  ) {
    return null;
  }
  if (value.decision === "allow") {
    return value.next_control_epoch === batch.epoch && value.next_nonce === batch.nonce
      ? value
      : null;
  }
  return value.next_control_epoch === batch.epoch + 1 &&
    typeof value.next_nonce === "string" &&
    value.next_nonce !== batch.nonce &&
    typeof value.decision_id === "string" &&
    value.decision_id &&
    typeof value.guidance === "string" &&
    value.guidance &&
    value.guidance_sha256 === sha256(value.guidance) &&
    typeof value.delivery_receipt_id === "string" &&
    /^delivery-[a-f0-9]{24}$/.test(value.delivery_receipt_id)
    ? value
    : null;
}

function artifactPaths(value: unknown): string[] {
  const output = new Set<string>();
  for (const text of collectStrings(value)) {
    const normalized = text.replaceAll("\\", "/");
    const candidates = normalized.match(/(?:\/workspace\/)?results\/[A-Za-z0-9_.@/-]+/g) || [];
    for (const candidate of candidates) {
      const relative = candidate.replace(/^\/workspace\//, "");
      if (!relative.split("/").includes("..")) output.add(relative);
    }
  }
  return Array.from(output).sort().slice(0, 24);
}

const READ_ONLY_TOOLS = new Set([
  "read",
  "read_file",
  "list",
  "list_files",
  "search",
  "grep",
  "find",
  "glob",
  "web_search",
  "web_fetch",
]);

export function classifyAction(toolName: string, params: JsonRecord): {
  consequential: boolean;
  reason: string;
} {
  const lower = toolName.toLowerCase();
  if (lower === "jarvis_control") return { consequential: false, reason: "control_ack" };
  if (READ_ONLY_TOOLS.has(lower)) return { consequential: false, reason: "read_only_tool" };
  const exec = classifyReadOnlyExec(lower, params);
  if (exec.reason !== "not_exec") {
    return {
      consequential: !exec.readOnly,
      reason: exec.readOnly ? "read_only_exec" : `exec_${exec.reason}`,
    };
  }
  return { consequential: true, reason: "mutation_or_unknown" };
}

function referencesControlPlane(params: JsonRecord): boolean {
  const raw = stableStringify(params);
  return [CONTROL_ROOT, REGISTRY_PATH, EVENT_PATH]
    .filter(Boolean)
    .some((target) => raw.includes(target));
}

async function waitForControl(sessionId: string): Promise<JsonRecord> {
  const deadline = Date.now() + Math.min(REVIEW_TIMEOUT_MS, 10_000);
  while (Date.now() < deadline) {
    const state = readControl(sessionId);
    if (Object.keys(state).length > 0) return state;
    await sleep(REVIEW_POLL_MS);
  }
  return {};
}

async function waitForIdentity(sessionIdOrKey: string): Promise<SessionIdentity | null> {
  const deadline = Date.now() + REGISTRATION_WAIT_MS;
  while (Date.now() < deadline) {
    const identity = resolveMasSessionIdentity(sessionIdOrKey);
    if (identity) return identity;
    await sleep(REVIEW_POLL_MS);
  }
  return null;
}

async function waitWhilePaused(sessionId: string): Promise<boolean> {
  const deadline = Date.now() + REVIEW_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const state = readControl(sessionId);
    const pause = asRecord(state.pause);
    if (!pause?.active) return true;
    await sleep(REVIEW_POLL_MS);
  }
  return false;
}

function batchFingerprint(fingerprints: string[]): string {
  return sha256(fingerprints.join("\0"));
}

function interruptEnvelope(response: JsonRecord, batch: ReviewBatch): string {
  return JSON.stringify({
    type: "JARVIS_HELD_ACTION_INVALIDATED_V1",
    judgment_id: response.decision_id,
    session_id: batch.sessionId,
    review_id: batch.reviewId,
    batch_id: batch.batchId,
    control_epoch: response.next_control_epoch,
    ack_nonce: response.next_nonce,
    batch_action_fingerprint: batchFingerprint(
      batch.actions.map((item) => item.actionFingerprint),
    ),
    action_fingerprints: batch.actions.map((item) => item.actionFingerprint),
    requester_guidance: response.guidance,
    required_ack_tool: "jarvis_control",
    next_step:
      "Acknowledge this exact envelope with jarvis_control, then revise at a new model boundary. Do not replay any old batch action.",
  });
}

async function finalizeBatch(batch: ReviewBatch): Promise<void> {
  if (batch.state !== "collecting") return;
  batch.state = "waiting";
  const heldActions = batch.actions.map((action) => ({
    action_id: action.actionId,
    tool_call_id: action.toolCallId,
    tool_name: action.toolName,
    action_fingerprint: action.actionFingerprint,
    params_sha256: action.paramsSha256,
    params_preview: action.paramsPreview,
    artifact_paths: action.artifactPaths,
  }));
  const expectedEventSeq = appendEvent("jarvis.review.requested", batch.sessionId, {
    run_id: batch.runId,
    turn_id: batch.turnId,
    batch_id: batch.batchId,
    action_id: batch.actions[0].actionId,
    review_id: batch.reviewId,
    control_epoch: batch.epoch,
    nonce: batch.nonce,
    held_actions: heldActions,
  });
  batch.expectedEventSeq = expectedEventSeq;
  const deadline = Date.now() + REVIEW_TIMEOUT_MS;
  let response: JsonRecord | null = null;
  let responseState: JsonRecord | null = null;
  while (Date.now() < deadline) {
    const state = readControl(batch.sessionId);
    const responses = Array.isArray(state.review_responses) ? state.review_responses : [];
    const candidate = responses.find(
      (item) => asRecord(item)?.review_id === batch.reviewId,
    );
    response = exactReviewResponse(candidate, {
      runId: batch.runId,
      sessionId: batch.sessionId,
      turnId: batch.turnId,
      batchId: batch.batchId,
      reviewId: batch.reviewId,
      epoch: batch.epoch,
      nonce: batch.nonce,
      expectedEventSeq: batch.expectedEventSeq,
      actionIds: batch.actions.map((item) => item.actionId),
      actionFingerprints: batch.actions.map((item) => item.actionFingerprint),
    });
    if (response) {
      responseState = state;
      break;
    }
    if (
      Number.isInteger(state.control_epoch) &&
      (Number(state.control_epoch) !== batch.epoch || state.nonce !== batch.nonce)
    ) {
      break;
    }
    await sleep(REVIEW_POLL_MS);
  }
  batches.delete(`${batch.runId}\0${batch.sessionId}\0${batch.turnId}\0${batch.epoch}\0${batch.nonce}`);
  batch.state = "resolved";
  if (!response) {
    appendEvent("jarvis.review.failed_closed", batch.sessionId, {
      run_id: batch.runId,
      turn_id: batch.turnId,
      batch_id: batch.batchId,
      action_id: batch.actions[0].actionId,
      review_id: batch.reviewId,
      expected_event_seq: expectedEventSeq,
      reason: "timeout_or_stale_generation",
    });
    for (const action of batch.actions) {
      action.resolve({
        block: true,
        blockReason:
          "JARVIS_CONTROL_UNAVAILABLE: the held action was not released; do not retry this old proposal.",
      });
    }
    return;
  }
  if (response.decision === "allow") {
    appendEvent("jarvis.review.allowed", batch.sessionId, {
      run_id: batch.runId,
      turn_id: batch.turnId,
      batch_id: batch.batchId,
      action_id: batch.actions[0].actionId,
      review_id: batch.reviewId,
      expected_event_seq: expectedEventSeq,
    });
    batch.actions.forEach((action) => action.resolve(undefined));
    return;
  }
  const envelope = interruptEnvelope(response, batch);
  const deliveryReceiptId = String(response.delivery_receipt_id || "");
  const deliveryRecords = Array.isArray(responseState?.delivery_receipts)
    ? responseState.delivery_receipts.map(asRecord).filter(Boolean)
    : [];
  const exactDelivery = deliveryRecords.find(
    (item) =>
      item?.receipt_id === deliveryReceiptId &&
      item?.decision_id === response?.decision_id &&
      item?.target_session_id === batch.sessionId &&
      item?.control_epoch === response?.next_control_epoch &&
      item?.nonce === response?.next_nonce &&
      item?.guidance_sha256 === response?.guidance_sha256,
  );
  if (!exactDelivery) {
    appendEvent("jarvis.review.failed_closed", batch.sessionId, {
      run_id: batch.runId,
      turn_id: batch.turnId,
      batch_id: batch.batchId,
      action_id: batch.actions[0].actionId,
      review_id: batch.reviewId,
      expected_event_seq: expectedEventSeq,
      reason: "delivery_receipt_missing_or_stale",
    });
    for (const action of batch.actions) {
      action.resolve({
        block: true,
        blockReason:
          "JARVIS_CONTROL_UNAVAILABLE: guidance lacked an exact delivery receipt; do not retry this old proposal.",
      });
    }
    return;
  }
  const pending: PendingAck = {
    sessionId: batch.sessionId,
    judgmentId: String(response.decision_id),
    controlEpoch: Number(response.next_control_epoch),
    ackNonce: String(response.next_nonce),
    batchActionFingerprint: batchFingerprint(
      batch.actions.map((item) => item.actionFingerprint),
    ),
    actionFingerprints: batch.actions.map((item) => item.actionFingerprint),
    deliveryReceiptId,
    guidanceSha256: String(response.guidance_sha256),
    envelopeSha256: sha256(envelope),
    expectedDeliveries: batch.actions.length,
    deliveredToolCallIds: new Set<string>(),
    deliveryEventEmitted: false,
    acknowledged: false,
  };
  pendingAcks.set(batch.sessionId, pending);
  appendEvent("jarvis.action.invalidated", batch.sessionId, {
    run_id: batch.runId,
    turn_id: batch.turnId,
    batch_id: batch.batchId,
    action_id: batch.actions[0].actionId,
    review_id: batch.reviewId,
    decision_id: response.decision_id,
    prior_control_epoch: batch.epoch,
    next_control_epoch: response.next_control_epoch,
    action_fingerprints: pending.actionFingerprints,
  });
  for (const [index, action] of batch.actions.entries()) {
    const persistedEnvelope =
      index === 0
        ? envelope
        : JSON.stringify({
            kind: "jarvis_batch_sibling_invalidated",
            judgment_id: response.decision_id,
            session_id: batch.sessionId,
            control_epoch: response.next_control_epoch,
            action_id: action.actionId,
            action_fingerprint: action.actionFingerprint,
            guidance_delivery: "canonical_first_batch_result",
          });
    pendingDeliveries.set(action.toolCallId, {
      sessionId: batch.sessionId,
      toolCallId: action.toolCallId,
      actionId: action.actionId,
      envelope: persistedEnvelope,
      envelopeSha256: sha256(persistedEnvelope),
      decisionId: String(response.decision_id),
      deliveryReceiptId,
      controlEpoch: Number(response.next_control_epoch),
      nonce: String(response.next_nonce),
      guidanceSha256: String(response.guidance_sha256),
    });
    action.resolve({ block: true, blockReason: persistedEnvelope });
  }
}

function holdAction(
  runId: string,
  sessionId: string,
  turnId: string,
  state: JsonRecord,
  action: Omit<HeldAction, "resolve">,
): Promise<unknown> {
  const epoch = Number(state.control_epoch);
  const nonce = String(state.nonce);
  const key = `${runId}\0${sessionId}\0${turnId}\0${epoch}\0${nonce}`;
  return new Promise((resolve) => {
    let batch = batches.get(key);
    if (!batch || batch.state !== "collecting") {
      batch = {
        runId,
        sessionId,
        turnId,
        batchId: `batch-${crypto.randomUUID()}`,
        reviewId: `review-${crypto.randomUUID()}`,
        epoch,
        nonce,
        expectedEventSeq: 0,
        actions: [],
        state: "collecting",
      };
      batches.set(key, batch);
      const exactBatch = batch as ReviewBatch;
      const exactKey = key;
      setTimeout(() => {
        void finalizeBatch(exactBatch).catch((error: unknown) => {
          batches.delete(exactKey);
          const pending = pendingAcks.get(exactBatch.sessionId);
          if (pending && !pending.acknowledged) {
            pendingAcks.delete(exactBatch.sessionId);
          }
          for (const action of exactBatch.actions) {
            pendingDeliveries.delete(action.toolCallId);
            action.resolve({
              block: true,
              blockReason:
                "JARVIS_CONTROL_UNAVAILABLE: deterministic batch finalization failed; do not retry this old proposal.",
            });
          }
          try {
            appendEvent("jarvis.review.failed_closed", exactBatch.sessionId, {
              run_id: exactBatch.runId,
              turn_id: exactBatch.turnId,
              batch_id: exactBatch.batchId,
              action_id: exactBatch.actions[0]?.actionId || "batch-finalization",
              review_id: exactBatch.reviewId,
              reason: "batch_finalization_error",
              error_type:
                error instanceof Error ? error.constructor.name : "UnknownError",
            });
          } catch {
            // Held promises are already resolved fail-closed. Event persistence
            // failure must never leave the worker or unrelated sessions hung.
          }
        });
      }, BATCH_SETTLE_MS);
    }
    batch.actions.push({ ...action, resolve });
  });
}

function pendingGuidance(state: JsonRecord): JsonRecord[] {
  if (!Array.isArray(state.guidance_queue)) return [];
  const persistedApplications = new Set(
    (Array.isArray(state.application_receipts) ? state.application_receipts : [])
      .map(asRecord)
      .filter((item): item is JsonRecord => Boolean(item))
      .map((item) => String(item.delivery_receipt_id || "")),
  );
  return state.guidance_queue
    .map(asRecord)
    .filter((item): item is JsonRecord => Boolean(item))
    .filter(
      (item) =>
        item.control_epoch === state.control_epoch &&
        item.nonce === state.nonce &&
        typeof item.receipt_id === "string" &&
        !persistedApplications.has(String(item.receipt_id)) &&
        !appliedReceiptIds.has(item.receipt_id),
    );
}

function renderGuidanceEnvelope(items: JsonRecord[], modelBoundaryId: string): string {
  return [
    "[JARVIS_AUTHENTICATED_REQUESTER_GUIDANCE_V1]",
    "This is narrowly routed requester evidence. Apply it only to the decision and scope shown; preserve unrelated completed work.",
    JSON.stringify({
      model_boundary_id: modelBoundaryId,
      decisions: items.map((item) => ({
        receipt_id: item.receipt_id,
        decision_id: item.decision_id,
        scope: item.scope,
        route: item.route,
        requester_guidance: item.text,
        guidance_sha256: item.guidance_sha256,
        control_epoch: item.control_epoch,
      })),
    }),
    "[/JARVIS_AUTHENTICATED_REQUESTER_GUIDANCE_V1]",
  ].join("\n");
}

const plugin = {
  id: PLUGIN_ID,
  name: "JarvisBench Dynamic MAS Supervisor",
  description: "Bounded, session-namespaced held-action bridge for formal dynamic MAS split.",
  version: PLUGIN_VERSION,
  configSchema: {
    validate(value: unknown) {
      return { ok: true, value: asRecord(value) || {} };
    },
    jsonSchema: { type: "object", additionalProperties: false, properties: {} },
  },
  register(api: any) {
    api.registerTool(
      {
        name: "jarvis_control",
        label: "Acknowledge Jarvis control",
        description:
          "Acknowledge one exact invalidated action batch before proposing a revised mutation.",
        parameters: {
          type: "object",
          additionalProperties: false,
          required: [
            "judgment_id",
            "session_id",
            "control_epoch",
            "ack_nonce",
            "action_fingerprint",
          ],
          properties: {
            judgment_id: { type: "string", minLength: 1 },
            session_id: { type: "string", minLength: 1 },
            control_epoch: { type: "integer", minimum: 0 },
            ack_nonce: { type: "string", minLength: 1 },
            action_fingerprint: { type: "string", minLength: 64, maxLength: 64 },
          },
        },
        execute: async (toolCallId: string, params: JsonRecord) => {
          const sessionId = controlToolSessions.get(toolCallId) || "";
          controlToolSessions.delete(toolCallId);
          const pending = pendingAcks.get(sessionId);
          const state = readControl(sessionId);
          const delivered = Boolean(
            pending && pending.deliveredToolCallIds.size === pending.expectedDeliveries,
          );
          const valid = Boolean(
            pending &&
              !pending.acknowledged &&
              delivered &&
              params.judgment_id === pending.judgmentId &&
              params.session_id === pending.sessionId &&
              params.control_epoch === pending.controlEpoch &&
              params.ack_nonce === pending.ackNonce &&
              params.action_fingerprint === pending.batchActionFingerprint &&
              state.control_epoch === pending.controlEpoch &&
              state.nonce === pending.ackNonce,
          );
          if (!valid || !pending) {
            appendEvent("control.ack.rejected", sessionId || PARENT_SESSION_ID, {
              action_id: "jarvis-control-ack",
              judgment_id: String(params.judgment_id || ""),
              reason: "exact_identity_or_delivery_receipt_mismatch",
            });
            const details = {
              status: "rejected",
              message: "Acknowledgement does not match the current delivered control envelope.",
            };
            return { content: [{ type: "text", text: JSON.stringify(details) }], details };
          }
          pending.acknowledged = true;
          appendEvent("control.ack.accepted", sessionId, {
            action_id: "jarvis-control-ack",
            judgment_id: pending.judgmentId,
            control_epoch: pending.controlEpoch,
            batch_action_fingerprint: pending.batchActionFingerprint,
          });
          if (!appliedReceiptIds.has(pending.deliveryReceiptId)) {
            const modelBoundaryId = `tool-continuation-${pending.judgmentId}`;
            appendEvent("control.guidance.applied", sessionId, {
              action_id: "guidance-application",
              delivery_receipt_id: pending.deliveryReceiptId,
              decision_id: pending.judgmentId,
              model_boundary_id: modelBoundaryId,
              control_epoch: pending.controlEpoch,
              nonce: pending.ackNonce,
              guidance_sha256: pending.guidanceSha256,
              envelope_sha256: pending.envelopeSha256,
              application_basis: "exact_tool_result_continuation_ack",
            });
            appliedReceiptIds.add(pending.deliveryReceiptId);
          }
          const details = {
            status: "acknowledged",
            judgment_id: pending.judgmentId,
            session_id: sessionId,
            control_epoch: pending.controlEpoch,
            next_step: "Propose a revised action. It will receive a new exact review.",
          };
          return { content: [{ type: "text", text: JSON.stringify(details) }], details };
        },
      },
      { name: "jarvis_control", optional: false },
    );

    appendEvent("jarvis.supervisor.loaded", PARENT_SESSION_ID, {
      plugin_id: PLUGIN_ID,
      plugin_version: PLUGIN_VERSION,
      control_protocol_version: CONTROL_PROTOCOL_VERSION,
      bounded_updates: true,
      raw_trace_included: false,
      per_session_control: true,
    });

    api.on("before_agent_start", (event: JsonRecord, ctx: JsonRecord) => {
      const sessionId = sessionKey(event, ctx);
      appendEvent("agent.start.observed", sessionId, {
        run_id: runKey(event, ctx),
        action_id: "agent-start",
      });
    });

    api.on(
      "before_prompt_build",
      (event: JsonRecord, ctx: JsonRecord) => {
        const sessionId = sessionKey(event, ctx);
        const turn = (turnBySession.get(sessionId) || 0) + 1;
        turnBySession.set(sessionId, turn);
        const turnId = `turn-${turn}`;
        const state = readControl(sessionId);
        appendEvent("agent.model_boundary", sessionId, {
          run_id: runKey(event, ctx),
          turn_id: turnId,
          action_id: "model-boundary",
          control_epoch: state.control_epoch || 0,
        });
        const items = pendingGuidance(state);
        if (items.length === 0) return undefined;
        const modelBoundaryId = `boundary-${crypto.randomUUID()}`;
        const envelope = renderGuidanceEnvelope(items, modelBoundaryId);
        injectedBySession.set(sessionId, { envelope, receipts: items, modelBoundaryId });
        appendEvent("control.guidance.delivered", sessionId, {
          run_id: runKey(event, ctx),
          turn_id: turnId,
          action_id: "guidance-delivery",
          model_boundary_id: modelBoundaryId,
          delivery_receipt_ids: items.map((item) => item.receipt_id),
          envelope_sha256: sha256(envelope),
        });
        return { appendSystemContext: envelope };
      },
      { priority: 1000 },
    );

    api.on("llm_input", (event: JsonRecord, ctx: JsonRecord) => {
      const sessionId = sessionKey(event, ctx);
      const injection = injectedBySession.get(sessionId);
      const input = collectStrings(event).join("\n");
      if (injection && input.includes(injection.envelope)) {
        for (const item of injection.receipts) {
          const receiptId = String(item.receipt_id || "");
          if (!receiptId || appliedReceiptIds.has(receiptId)) continue;
          appendEvent("control.guidance.applied", sessionId, {
            run_id: runKey(event, ctx),
            turn_id: `turn-${turnBySession.get(sessionId) || 0}`,
            action_id: "guidance-application",
            delivery_receipt_id: receiptId,
            decision_id: item.decision_id,
            model_boundary_id: injection.modelBoundaryId,
            control_epoch: item.control_epoch,
            nonce: item.nonce,
            guidance_sha256: item.guidance_sha256,
            envelope_sha256: sha256(injection.envelope),
          });
          appliedReceiptIds.add(receiptId);
        }
        injectedBySession.delete(sessionId);
      }
      appendEvent("agent.llm.input", sessionId, {
        run_id: runKey(event, ctx),
        action_id: "llm-input",
        provider: event.provider,
        model: event.model,
        raw_trace_included: false,
      });
    });

    api.on("llm_output", (event: JsonRecord, ctx: JsonRecord) => {
      const sessionId = sessionKey(event, ctx);
      appendEvent("agent.llm.output", sessionId, {
        run_id: runKey(event, ctx),
        action_id: "llm-output",
        provider: event.provider,
        model: event.model,
        assistant_preview: compact(event.assistantTexts || event.content || ""),
        raw_trace_included: false,
      });
    });

    api.on(
      "before_tool_call",
      async (event: JsonRecord, ctx: JsonRecord) => {
        const sessionId = sessionKey(event, ctx);
        const toolName = String(event.toolName || "tool");
        const toolCallId = String(event.toolCallId || ctx.toolCallId || crypto.randomUUID());
        const params = asRecord(event.params) || {};
        if (toolName.toLowerCase() === "jarvis_control") {
          controlToolSessions.set(toolCallId, sessionId);
          return undefined;
        }
        if (referencesControlPlane(params)) {
          appendEvent("jarvis.control_plane.access_blocked", sessionId, {
            run_id: runKey(event, ctx),
            action_id: `action-${crypto.randomUUID()}`,
            tool_call_id: toolCallId,
            tool_name: toolName,
          });
          return {
            block: true,
            blockReason:
              "JARVIS_CONTROL_PLANE_PROTECTED: worker access to host-owned control state is prohibited.",
          };
        }
        let identity = resolveMasSessionIdentity(sessionId);
        const classification = classifyAction(toolName, params);
        const actionId = `action-${crypto.randomUUID()}`;
        const paramsRaw = stableStringify(params);
        const paramsSha256 = sha256(paramsRaw);
        const artifacts = artifactPaths(params);
        const fingerprint = sha256(
          stableStringify({
            tool_name: toolName,
            params_sha256: paramsSha256,
            artifact_paths: artifacts,
          }),
        );
        appendEvent("agent.tool.proposed", sessionId, {
          run_id: runKey(event, ctx),
          action_id: actionId,
          tool_call_id: toolCallId,
          tool_name: toolName,
          params_sha256: paramsSha256,
          action_fingerprint: fingerprint,
          artifact_paths: artifacts,
          review_classification: classification.reason,
        });

        if (!AUTONOMOUS_REVIEW || isExecutionManagerSession(sessionId, identity)) {
          return undefined;
        }
        // sessions_spawn registers children dynamically.  A child can reach
        // its first tool hook just before the host registry poll observes the
        // native sessions index.  Wait briefly for that exact binding; never
        // let an unregistered child mutation bypass the held-action protocol.
        if (!identity && DYNAMIC_REQUIRED) identity = await waitForIdentity(sessionId);
        if (!identity) {
          return DYNAMIC_REQUIRED && classification.consequential
            ? {
                block: true,
                blockReason:
                  "JARVIS_SESSION_UNREGISTERED: consequential action was held because exact dynamic session registration was unavailable.",
              }
            : undefined;
        }
        if (identity.role !== "worker") return undefined;
        if (!(await waitWhilePaused(sessionId))) {
          return {
            block: true,
            blockReason: "JARVIS_SESSION_PAUSE_TIMEOUT: this session remained paused.",
          };
        }
        const pending = pendingAcks.get(sessionId);
        if (pending && !pending.acknowledged) {
          return {
            block: true,
            blockReason:
              "JARVIS_ACK_REQUIRED: call jarvis_control with the exact delivered judgment, session, epoch, nonce, and batch action fingerprint before another mutation.",
          };
        }
        if (!classification.consequential) {
          appendEvent("jarvis.action.released", sessionId, {
            run_id: runKey(event, ctx),
            action_id: actionId,
            tool_call_id: toolCallId,
            tool_name: toolName,
            reason: classification.reason,
          });
          return undefined;
        }
        let state = readControl(sessionId);
        if (Object.keys(state).length === 0) state = await waitForControl(sessionId);
        if (Object.keys(state).length === 0) {
          return DYNAMIC_REQUIRED
            ? {
                block: true,
                blockReason:
                  "JARVIS_SESSION_UNREGISTERED: consequential action was held because exact session control was unavailable.",
              }
            : undefined;
        }
        return holdAction(
          runKey(event, ctx),
          sessionId,
          `turn-${turnBySession.get(sessionId) || 0}`,
          state,
          {
            actionId,
            toolCallId,
            toolName,
            paramsSha256,
            actionFingerprint: fingerprint,
            paramsPreview: compact(params),
            artifactPaths: artifacts,
          },
        );
      },
      { priority: 1000 },
    );

    api.on("after_tool_call", (event: JsonRecord, ctx: JsonRecord) => {
      const sessionId = sessionKey(event, ctx);
      appendEvent("agent.tool.output", sessionId, {
        run_id: runKey(event, ctx),
        action_id: "tool-output",
        tool_call_id: String(event.toolCallId || ctx.toolCallId || "tool-call"),
        tool_name: String(event.toolName || "tool"),
        result_preview: compact(event.result || ""),
        error: Boolean(event.error),
        raw_trace_included: false,
      });
    });

    api.on(
      "tool_result_persist",
      (event: JsonRecord, ctx: JsonRecord) => {
        const toolCallId = String(event.toolCallId || ctx.toolCallId || "");
        const delivery = pendingDeliveries.get(toolCallId);
        if (!delivery) return undefined;
        const persisted = collectStrings(event.message).join("\n");
        if (!persisted.includes(delivery.envelope)) {
          appendEvent("control.interrupt.delivery_rejected", delivery.sessionId, {
            action_id: delivery.actionId,
            tool_call_id: toolCallId,
            reason: "exact_envelope_not_persisted",
          });
          return undefined;
        }
        const pending = pendingAcks.get(delivery.sessionId);
        if (pending) pending.deliveredToolCallIds.add(toolCallId);
        appendEvent("control.interrupt.delivered", delivery.sessionId, {
          action_id: delivery.actionId,
          tool_call_id: toolCallId,
          envelope_sha256: delivery.envelopeSha256,
          exact: true,
        });
        if (
          pending &&
          !pending.deliveryEventEmitted &&
          pending.deliveredToolCallIds.size === pending.expectedDeliveries
        ) {
          pending.deliveryEventEmitted = true;
          appendEvent("control.guidance.delivered", delivery.sessionId, {
            action_id: "guidance-delivery",
            tool_call_ids: [...pending.deliveredToolCallIds].sort(),
            delivery_receipt_id: delivery.deliveryReceiptId,
            decision_id: delivery.decisionId,
            control_epoch: delivery.controlEpoch,
            nonce: delivery.nonce,
            guidance_sha256: delivery.guidanceSha256,
            envelope_sha256: pending.envelopeSha256,
            delivery_route: "held_action_batch_tool_results",
            exact: true,
          });
        }
        pendingDeliveries.delete(toolCallId);
        return undefined;
      },
      { priority: -1000 },
    );

    api.on("agent_end", (event: JsonRecord, ctx: JsonRecord) => {
      const sessionId = sessionKey(event, ctx);
      appendEvent("agent.final.observed", sessionId, {
        run_id: runKey(event, ctx),
        action_id: "agent-final",
        success: event.success === true,
        result_preview: compact(event.result || event.output || "", 1_200),
        raw_trace_included: false,
      });
    });

    if (PLUGIN_ROLE === "gateway") {
      const ready = {
        schema_version: "1.0",
        ready: true,
        plugin_id: PLUGIN_ID,
        plugin_version: PLUGIN_VERSION,
        control_protocol_version: CONTROL_PROTOCOL_VERSION,
        project_id: PROJECT_ID,
        hooks_registration_complete: true,
        native_gateway_required: true,
      };
      const readySeq = appendEvent("jarvis.supervisor.gateway_ready", PARENT_SESSION_ID, {
        action_id: "plugin-ready",
        ...ready,
      });
      if (PLUGIN_READY_PATH) {
        writePrivateJson(PLUGIN_READY_PATH, { ...ready, ready_event_seq: readySeq });
      }
    }
  },
};

export default plugin;
