import { EventEmitter } from "node:events";

import { describe, expect, it, vi } from "vitest";

import { CodeCompassClient, DaemonError, SpawnFn } from "../src/cli/client";

/** Fake daemon process: captures writes, emits stdout/stderr on demand. */
class FakeChild extends EventEmitter {
  readonly written: string[] = [];
  killed = false;
  readonly stdin = {
    write: (chunk: string): boolean => {
      this.written.push(chunk);
      return true;
    },
  };
  readonly stdout = new EventEmitter();
  readonly stderr = new EventEmitter();

  kill(): boolean {
    this.killed = true;
    return true;
  }

  /** Emit one complete response line on stdout. */
  respond(message: unknown): void {
    this.stdout.emit("data", Buffer.from(JSON.stringify(message) + "\n"));
  }

  /** Emit raw text on stdout (for chunk-splitting tests). */
  emitOut(text: string): void {
    this.stdout.emit("data", Buffer.from(text));
  }

  requests(): Array<{ id: number; method: string; params: Record<string, unknown> }> {
    return this.written.map((line) => JSON.parse(line.trim()));
  }
}

interface SpawnCall {
  command: string;
  args: string[];
  options: { cwd?: string; env?: NodeJS.ProcessEnv };
}

function makeClient(
  overrides: Partial<ConstructorParameters<typeof CodeCompassClient>[0]> = {}
): { client: CodeCompassClient; children: FakeChild[]; spawnCalls: SpawnCall[] } {
  const children: FakeChild[] = [];
  const spawnCalls: SpawnCall[] = [];
  const spawnImpl: SpawnFn = (command, args, options) => {
    const child = new FakeChild();
    children.push(child);
    spawnCalls.push({ command, args, options });
    return child;
  };
  const client = new CodeCompassClient({
    pythonPath: "python3",
    repoRoot: "/repo",
    spawnImpl,
    ...overrides,
  });
  return { client, children, spawnCalls };
}

describe("CodeCompassClient", () => {
  it("spawns the daemon with --repo and protocol env", () => {
    const { client, children, spawnCalls } = makeClient({ llmProvider: "ollama" });
    void client.status();

    expect(children).toHaveLength(1);
    expect(spawnCalls[0].command).toBe("python3");
    expect(spawnCalls[0].args).toEqual([
      "-m",
      "codecompass.cli.main",
      "daemon",
      "--repo",
      "/repo",
    ]);
    expect(spawnCalls[0].options.env?.PYTHONUNBUFFERED).toBe("1");
    expect(spawnCalls[0].options.env?.CODECOMPASS_LLM_PROVIDER).toBe("ollama");
    expect(children[0].requests()[0].method).toBe("status");
    expect(children[0].written[0]).toContain('"id":1');
  });

  it("resolves a sync request with its result payload", async () => {
    const { client, children } = makeClient();
    const promise = client.findSymbol("login");
    await Promise.resolve();

    expect(children[0].requests()[0]).toEqual({
      id: 1,
      method: "find_symbol",
      params: { name: "login" },
    });
    children[0].respond({
      id: 1,
      result: { found: true, name: "login", count: 1, symbols: [] },
    });
    await expect(promise).resolves.toMatchObject({ found: true, count: 1 });
  });

  it("streams tokens before resolving the done payload", async () => {
    const { client, children } = makeClient();
    const tokens: string[] = [];
    const promise = client.query("where is auth?", 5, (token) => tokens.push(token));
    await Promise.resolve();

    children[0].respond({ id: 1, stream: "Auth " });
    children[0].respond({ id: 1, stream: "lives here." });
    children[0].respond({
      id: 1,
      done: true,
      question: "where is auth?",
      count: 1,
      sources: [{ file: "app/auth.py", line_start: 3 }],
    });

    const result = await promise;
    expect(tokens).toEqual(["Auth ", "lives here."]);
    expect(result.count).toBe(1);
    expect(result.sources[0].file).toBe("app/auth.py");
  });

  it("rejects with the daemon's JSON-RPC error code", async () => {
    const { client, children } = makeClient();
    const promise = client.trace("missing");
    await Promise.resolve();

    children[0].respond({
      id: 1,
      error: { code: -32603, message: "Repository /repo is not indexed." },
    });

    const error = await promise.then(
      () => undefined,
      (err: unknown) => err as DaemonError
    );
    expect(error).toBeInstanceOf(DaemonError);
    expect(error?.code).toBe(-32603);
    expect(error?.message).toContain("is not indexed");
  });

  it("ignores malformed lines and unknown ids", async () => {
    const { client, children } = makeClient();
    const promise = client.status();
    await Promise.resolve();

    children[0].emitOut("this is not json\n");
    children[0].respond({ id: 99, result: { ignored: true } });
    children[0].respond({ id: 1, result: { indexed: true } });

    await expect(promise).resolves.toMatchObject({ indexed: true });
  });

  it("reassembles lines split across stdout chunks", async () => {
    const { client, children } = makeClient();
    const promise = client.status();
    await Promise.resolve();

    children[0].emitOut('{"id":1,"resu');
    children[0].emitOut('lt":{"indexed":false}}\n');

    await expect(promise).resolves.toMatchObject({ indexed: false });
  });

  it("rejects pending requests and reports when the daemon exits", async () => {
    const onExit = vi.fn();
    const { client, children } = makeClient({ onExit });
    const promise = client.status();
    await Promise.resolve();

    children[0].emit("exit", 1);

    await expect(promise).rejects.toThrow("exited (code 1)");
    expect(onExit).toHaveBeenCalledWith(1);
    expect(client.isRunning).toBe(false);
  });

  it("dispose kills the daemon silently (no crash callback)", async () => {
    const onExit = vi.fn();
    const { client, children } = makeClient({ onExit });
    const promise = client.status();
    await Promise.resolve();

    client.dispose();

    expect(children[0].killed).toBe(true);
    expect(onExit).not.toHaveBeenCalled();
    await expect(promise).rejects.toThrow("disposed");
    await expect(client.status()).rejects.toThrow("disposed");
  });

  it("restart replaces the daemon process", () => {
    const { client, children } = makeClient();
    client.start();
    expect(children).toHaveLength(1);

    client.restart();
    expect(children[0].killed).toBe(true);
    expect(children).toHaveLength(2);
    expect(client.isRunning).toBe(true);
    client.dispose();
  });

  it("maps typed wrappers onto daemon methods and params", async () => {
    const { client, children } = makeClient();
    void client.trace("checkout");
    void client.findCallers("checkout");
    void client.explain("app/cart.py", 3, 9);
    await Promise.resolve();

    const requests = children[0].requests();
    expect(requests[0]).toEqual({ id: 1, method: "trace", params: { symbol: "checkout", depth: 3 } });
    expect(requests[1]).toEqual({
      id: 2,
      method: "find_callers",
      params: { symbol: "checkout" },
    });
    expect(requests[2]).toEqual({
      id: 3,
      method: "explain",
      params: { file: "app/cart.py", start: 3, end: 9 },
    });

    // settle everything so no dangling promises leak
    children[0].respond({ id: 1, result: { found: false } });
    children[0].respond({ id: 2, result: { count: 0, callers: [], symbol: "checkout" } });
    children[0].respond({ id: 3, done: true, file: "app/cart.py", line_start: 3, line_end: 9, mode: "explain" });
  });
});
