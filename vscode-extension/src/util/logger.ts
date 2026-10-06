import * as vscode from "vscode";

export interface Logger {
  info(message: string): void;
  warn(message: string): void;
  error(message: string): void;
  /** Append a raw (already formatted) line, e.g. daemon stderr. */
  raw(message: string): void;
}

/** Timestamped OutputChannel logger ("View → Output → CodeCompass"). */
export function createLogger(context: vscode.ExtensionContext): Logger {
  const channel = vscode.window.createOutputChannel("CodeCompass");
  context.subscriptions.push(channel);
  const stamp = (): string => new Date().toISOString().slice(11, 19);
  return {
    info: (message) => channel.appendLine(`[${stamp()}] ${message}`),
    warn: (message) => channel.appendLine(`[${stamp()}] WARN ${message}`),
    error: (message) => channel.appendLine(`[${stamp()}] ERROR ${message}`),
    raw: (message) => channel.appendLine(message),
  };
}
