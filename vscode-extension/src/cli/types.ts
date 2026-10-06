/**
 * TypeScript mirrors of the Python handler schemas (src/codecompass/api/handlers.py).
 * Every interface here documents the JSON that actually flows over the daemon
 * protocol — keep them in sync with the Python side.
 */

/** One symbol location (find_symbol). */
export interface SymbolInfo {
  name: string;
  kind: string | null;
  file: string;
  line_start: number | null;
  line_end: number | null;
  signature: string | null;
  docstring: string | null;
  complexity: number | null;
}

export interface FindSymbolResult {
  found: boolean;
  name: string;
  count: number;
  symbols: SymbolInfo[];
}

/** A caller/callee entry; `call_line` is the call site when known. */
export interface CallEntry {
  name: string;
  kind: string | null;
  file: string;
  line_start: number | null;
  line_end: number | null;
  signature: string | null;
  call_line?: number | null;
}

export interface FindCallersResult {
  symbol: string;
  callers: CallEntry[];
  count: number;
}

export interface FindCalleesResult {
  symbol: string;
  callees: CallEntry[];
  count: number;
}

export interface GraphNode {
  id: string;
  name: string;
  kind: string | null;
  file: string;
  line_start: number | null;
  line_end: number | null;
  signature: string | null;
}

export interface GraphEdge {
  from: string;
  to: string;
  line: number | null;
}

/** Flat node/edge payload consumed by the graph webview (trace). */
export interface TraceResult {
  root: string;
  found: boolean;
  depth: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
  node_count: number;
  edge_count: number;
}

export interface ImpactResult {
  symbol: string;
  found: boolean;
  risk: string;
  direct_callers: number;
  total_affected: number;
  affected_files: string[];
  affected_tests: string[];
  reasoning: string;
}

/** One citation row of the chat sources table (query). */
export interface Source {
  file: string | null;
  line_start: number | null;
  line_end: number | null;
  symbol_name: string | null;
  score: number;
}

/** Final `done` payload of a streaming `query` request. */
export interface QueryDone {
  question: string;
  count: number;
  sources: Source[];
}

/** Final `done` payload of a streaming `explain`/`simplify` request. */
export interface ReviewDone {
  file: string;
  line_start: number;
  line_end: number;
  mode: string;
}

export interface IndexStats {
  repo_root: string;
  force: boolean;
  ok: boolean;
  files: number;
  symbols: number;
  calls: number;
  imports: number;
  chunks: number;
  duration_seconds: number;
}

export interface StatusResult {
  indexed: boolean;
  repo_root: string | null;
  state: string | null;
  last_indexed_at: string | null;
  files: number;
  symbols: number;
  chunks: number;
  llm_provider: string;
  llm_model: string;
  version: string;
}

export interface Hotspot {
  name: string | null;
  kind: string | null;
  file: string | null;
  line: number | null;
  complexity: number;
}

export interface HotspotsResult {
  threshold: number;
  count: number;
  hotspots: Hotspot[];
}

export interface CouplingModule {
  file: string | null;
  imports: number;
  imported_by: number;
  total: number;
}

export interface CouplingResult {
  threshold: number;
  count: number;
  modules: CouplingModule[];
}

export interface CircularResult {
  circular: boolean;
  count: number;
  cycle: Array<{ from: string; to: string }>;
}

// ---------------------------------------------------------------------------
// Wire protocol (one JSON object per line)
// ---------------------------------------------------------------------------

export interface RpcError {
  code: number;
  message: string;
}

/**
 * Any daemon -> client message. Responses are polymorphic on purpose:
 * `result` for sync methods, `stream`/`done` for streaming methods,
 * `error` for failures (the `done` payload fields are flattened alongside
 * `id`/`done`, e.g. `{id, done, count, sources}`).
 */
export interface DaemonMessage {
  id?: number | null;
  result?: unknown;
  error?: RpcError;
  stream?: string;
  done?: boolean;
  [key: string]: unknown;
}

/** JSON-RPC 2.0 error codes used by the daemon. */
export const ErrorCodes = {
  parse: -32700,
  invalidRequest: -32600,
  methodNotFound: -32601,
  invalidParams: -32602,
  internal: -32603,
} as const;
