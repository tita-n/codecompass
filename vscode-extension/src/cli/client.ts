import { spawn as nodeSpawn } from "child_process";

import type {
  CircularResult,
  CouplingResult,
  DaemonMessage,
  FindCalleesResult,
  FindCallersResult,
  FindSymbolResult,
  HotspotsResult,
  ImpactResult,
  IndexStats,
  QueryDone,
  ReviewDone,
  StatusResult,
  TraceResult,
} from "./types";

/** Minimal shape of the child process the client needs (mockable in tests). */
export interface SpawnableChild {
  stdin: { write(chunk: string): boolean } | null;
  stdout: { on(event: "data", listener: (chunk: Buffer | string) => void): unknown } | null;
  stderr: { on(event: "data", listener: (chunk: Buffer | string) => void): unknown } | null;
  on(event: "exit", listener: (code: number | null) => void): unknown;
  on(event: "error", listener: (error: Error) => void): unknown;
  kill(): boolean;
}

export type SpawnFn = (
  command: string,
  args: string[],
  options: { cwd?: string; env?: NodeJS.ProcessEnv; stdio?: unknown }
) => SpawnableChild;

/** Error raised for daemon-side failures; `code` mirrors the JSON-RPC code. */
export class DaemonError extends Error {
  constructor(
    message: string,
    readonly code?: number,
    readonly method?: string
  ) {
    super(message);
    this.name = "DaemonError";
  }
}

export interface ClientOptions {
  /** Interpreter with the codecompass package installed (the `codecompass.pythonPath` setting). */
  pythonPath: string;
  /** Repository root passed as `--repo`. */
  repoRoot: string;
  /** `codecompass.llmProvider` setting; exported as CODECOMPASS_LLM_PROVIDER. */
  llmProvider?: string;
  /** Extra environment for the daemon process. */
  env?: NodeJS.ProcessEnv;
  /** Raw stderr lines (daemon diagnostics) for the output channel. */
  onStderr?: (line: string) => void;
  /** Fired whenever the daemon process exits (crash or restart). */
  onExit?: (code: number | null) => void;
  /** Injection point for tests. */
  spawnImpl?: SpawnFn;
}

interface Pending {
  method: string;
  resolve: (message: DaemonMessage) => void;
  reject: (error: Error) => void;
  onToken?: (token: string) => void;
}

const defaultSpawn: SpawnFn = (command, args, options) =>
  nodeSpawn(command, args, {
    ...options,
    stdio: ["pipe", "pipe", "pipe"],
  }) as unknown as SpawnableChild;

/**
 * Newline-delimited JSON-RPC client for `codecompass daemon`.
 *
 * One process per client; requests are correlated by numeric id. Streaming
 * methods resolve with the final `done` message after delivering every
 * `stream` chunk to the caller's token callback.
 */
export class CodeCompassClient {
  private child: SpawnableChild | null = null;
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();
  private buffer = "";
  private disposed = false;

  readonly repoRoot: string;

  constructor(private readonly options: ClientOptions) {
    this.repoRoot = options.repoRoot;
  }

  /** True while the daemon process is running. */
  get isRunning(): boolean {
    return this.child !== null;
  }

  start(): void {
    if (this.child || this.disposed) {
      return;
    }
    const { pythonPath, repoRoot, llmProvider } = this.options;
    const args = ["-m", "codecompass.cli.main", "daemon", "--repo", repoRoot];
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      ...(this.options.env ?? {}),
      PYTHONUNBUFFERED: "1",
    };
    if (llmProvider) {
      // Consumed by codecompass.utils.config.get_config (daemon processes only).
      env.CODECOMPASS_LLM_PROVIDER = llmProvider;
    }

    const spawnFn = this.options.spawnImpl ?? defaultSpawn;
    const child = spawnFn(pythonPath, args, { cwd: repoRoot, env });
    this.child = child;

    child.stdout?.on("data", (chunk) => this.onData(chunk.toString()));
    child.stderr?.on("data", (chunk) => {
      const text = chunk.toString();
      for (const line of text.split("\n")) {
        if (line.trim()) {
          this.options.onStderr?.(line);
        }
      }
    });
    child.on("error", (error) => {
      if (this.child !== child) {
        return; // stale event from a replaced process
      }
      this.failAll(new DaemonError(`Cannot start daemon: ${error.message}`));
      this.child = null;
      this.options.onExit?.(null);
    });
    child.on("exit", (code) => {
      if (this.child !== child) {
        return; // stale event: dispose()/restart() already replaced this child
      }
      this.child = null;
      this.failAll(
        new DaemonError(`CodeCompass daemon exited (code ${code ?? "unknown"})`, code ?? undefined)
      );
      this.options.onExit?.(code);
    });
  }

  /** Kill the daemon (if any) and start a fresh one. */
  restart(): void {
    this.dispose();
    this.disposed = false;
    this.start();
  }

  /** Kill the daemon; pending requests reject; no crash callbacks afterwards. */
  dispose(): void {
    this.disposed = true;
    this.failAll(new DaemonError("CodeCompass client disposed"));
    const child = this.child;
    this.child = null; // detach first so a (synchronous) exit event is ignored
    if (child) {
      try {
        child.kill();
      } catch {
        // process already gone
      }
    }
    this.buffer = "";
  }

  private onData(text: string): void {
    this.buffer += text;
    let newline = this.buffer.indexOf("\n");
    while (newline >= 0) {
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      if (line) {
        this.handleLine(line);
      }
      newline = this.buffer.indexOf("\n");
    }
  }

  /** Route one response line. Package-private: exercised directly by tests. */
  handleLine(line: string): void {
    let message: DaemonMessage;
    try {
      message = JSON.parse(line) as DaemonMessage;
    } catch {
      return; // never let malformed output kill the stream
    }
    const id = typeof message.id === "number" ? message.id : undefined;
    if (id === undefined) {
      return;
    }
    const entry = this.pending.get(id);
    if (!entry) {
      return;
    }
    if (typeof message.stream === "string") {
      entry.onToken?.(message.stream);
      return;
    }
    if (message.error) {
      this.pending.delete(id);
      entry.reject(new DaemonError(message.error.message, message.error.code, entry.method));
      return;
    }
    if (message.done === true || "result" in message) {
      this.pending.delete(id);
      entry.resolve(message);
    }
  }

  private failAll(error: Error): void {
    for (const entry of this.pending.values()) {
      entry.reject(error);
    }
    this.pending.clear();
  }

  /** Send a request; resolves with the raw response message. */
  send(
    method: string,
    params: Record<string, unknown>,
    onToken?: (token: string) => void
  ): Promise<DaemonMessage> {
    if (this.disposed) {
      return Promise.reject(new DaemonError("CodeCompass client disposed"));
    }
    this.start();
    if (!this.child) {
      return Promise.reject(new DaemonError("CodeCompass daemon is not running"));
    }
    const id = this.nextId++;
    const promise = new Promise<DaemonMessage>((resolve, reject) => {
      this.pending.set(id, { method, resolve, reject, onToken });
    });
    const line = JSON.stringify({ id, method, params }) + "\n";
    this.child.stdin?.write(line);
    return promise;
  }

  /** Sync request: resolves the `result` payload. */
  async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    const message = await this.send(method, params);
    return message.result as T;
  }

  /** Streaming request: resolves the flattened `done` payload. */
  streamRequest<T>(
    method: string,
    params: Record<string, unknown>,
    onToken?: (token: string) => void
  ): Promise<T> {
    return this.send(method, params, onToken) as Promise<T>;
  }

  // -------------------------------------------------------------------------
  // Typed methods (one per daemon method)
  // -------------------------------------------------------------------------

  findSymbol(name: string, kind?: string): Promise<FindSymbolResult> {
    return this.request<FindSymbolResult>("find_symbol", kind ? { name, kind } : { name });
  }

  findCallers(symbol: string): Promise<FindCallersResult> {
    return this.request<FindCallersResult>("find_callers", { symbol });
  }

  findCallees(symbol: string): Promise<FindCalleesResult> {
    return this.request<FindCalleesResult>("find_callees", { symbol });
  }

  trace(symbol: string, depth = 3): Promise<TraceResult> {
    return this.request<TraceResult>("trace", { symbol, depth });
  }

  impact(symbol: string, maxDepth = 3): Promise<ImpactResult> {
    return this.request<ImpactResult>("impact", { symbol, max_depth: maxDepth });
  }

  index(repoPath: string, force = false): Promise<IndexStats> {
    return this.request<IndexStats>("index", { repo: repoPath, force });
  }

  status(): Promise<StatusResult> {
    return this.request<StatusResult>("status", {});
  }

  analyzeHotspots(threshold = 10): Promise<HotspotsResult> {
    return this.request<HotspotsResult>("analyze_hotspots", { threshold });
  }

  analyzeCoupling(threshold = 5): Promise<CouplingResult> {
    return this.request<CouplingResult>("analyze_coupling", { threshold });
  }

  analyzeCircular(): Promise<CircularResult> {
    return this.request<CircularResult>("analyze_circular", {});
  }

  query(question: string, limit = 5, onToken?: (token: string) => void): Promise<QueryDone> {
    return this.streamRequest<QueryDone>("query", { question, limit }, onToken);
  }

  explain(
    file: string,
    start: number,
    end: number,
    onToken?: (token: string) => void
  ): Promise<ReviewDone> {
    return this.streamRequest<ReviewDone>("explain", { file, start, end }, onToken);
  }

  simplify(
    file: string,
    start: number,
    end: number,
    onToken?: (token: string) => void
  ): Promise<ReviewDone> {
    return this.streamRequest<ReviewDone>("simplify", { file, start, end }, onToken);
  }

  shutdown(): Promise<unknown> {
    return this.request<unknown>("shutdown", {});
  }
}


