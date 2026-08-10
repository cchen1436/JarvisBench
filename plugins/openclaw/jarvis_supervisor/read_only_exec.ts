type JsonRecord = Record<string, unknown>;

export type ReadOnlyExecClassification = {
  readOnly: boolean;
  reason:
    | "not_exec"
    | "empty"
    | "shell_control"
    | "unknown_command"
    | "mutation_flag"
    | "read_only";
  command: string;
};

const EXEC_NAMES = new Set(["exec", "shell", "bash", "command", "run"]);
const READ_ONLY_COMMANDS = new Set([
  "awk",
  "basename",
  "cat",
  "cksum",
  "column",
  "comm",
  "cut",
  "date",
  "diff",
  "dirname",
  "du",
  "env",
  "file",
  "find",
  "git",
  "grep",
  "head",
  "jq",
  "ls",
  "md5sum",
  "od",
  "pwd",
  "readlink",
  "rg",
  "sed",
  "sha1sum",
  "sha256sum",
  "sort",
  "stat",
  "tail",
  "test",
  "tr",
  "tree",
  "uniq",
  "wc",
  "which",
  "xargs",
]);
const GIT_READ_ONLY = new Set([
  "blame",
  "branch",
  "diff",
  "grep",
  "log",
  "ls-files",
  "rev-parse",
  "show",
  "status",
]);
const MUTATION_FLAGS = new Set([
  "-delete",
  "--delete",
  "--exec",
  "--execdir",
  "-exec",
  "-execdir",
  "-i",
  "--in-place",
  "--output",
  "-o",
]);

function commandText(params: JsonRecord): string {
  for (const name of ["command", "cmd", "script", "input"]) {
    const value = params[name];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function tokens(command: string): string[] | null {
  // Fail closed on shell structure, expansion, redirection, assignment, or
  // quoting that would require a complete shell parser to classify safely.
  if (/[;&|><`\n\r]/.test(command) || /\$[({A-Za-z_]/.test(command)) return null;
  const matches = command.match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g);
  if (!matches) return [];
  return matches.map((item) => item.replace(/^(?:"|')|(?:"|')$/g, ""));
}

export function classifyReadOnlyExec(
  toolName: string,
  params: JsonRecord,
): ReadOnlyExecClassification {
  if (!EXEC_NAMES.has(toolName.toLowerCase())) {
    return { readOnly: false, reason: "not_exec", command: "" };
  }
  const command = commandText(params);
  if (!command) return { readOnly: false, reason: "empty", command };
  const parsed = tokens(command);
  if (!parsed) return { readOnly: false, reason: "shell_control", command };
  if (parsed.length === 0) return { readOnly: false, reason: "empty", command };
  let executableIndex = 0;
  while (
    executableIndex < parsed.length &&
    /^[A-Za-z_][A-Za-z0-9_]*=/.test(parsed[executableIndex])
  ) {
    executableIndex += 1;
  }
  const executable = (parsed[executableIndex] || "").split("/").pop() || "";
  const args = parsed.slice(executableIndex + 1);
  if (!READ_ONLY_COMMANDS.has(executable)) {
    return { readOnly: false, reason: "unknown_command", command };
  }
  if (args.some((item) => MUTATION_FLAGS.has(item))) {
    return { readOnly: false, reason: "mutation_flag", command };
  }
  if (executable === "git") {
    const subcommand = args.find((item) => !item.startsWith("-")) || "";
    if (!GIT_READ_ONLY.has(subcommand)) {
      return { readOnly: false, reason: "unknown_command", command };
    }
  }
  if (executable === "find" && args.some((item) => item === "-ok" || item === "-okdir")) {
    return { readOnly: false, reason: "mutation_flag", command };
  }
  return { readOnly: true, reason: "read_only", command };
}
