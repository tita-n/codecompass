import * as vscode from "vscode";

import type { CodeCompassClient } from "../cli/client";

export type StatusKind = "ready" | "empty" | "indexing" | "offline";

/**
 * Right-side status bar item: click focuses the Chat view.
 * All state transitions keep a one-line, icon-prefixed label.
 */
export class StatusBarController implements vscode.Disposable {
  private readonly item: vscode.StatusBarItem;

  constructor() {
    this.item = vscode.window.createStatusBarItem("codecompass.status", vscode.StatusBarAlignment.Right, 90);
    this.item.command = "codecompass.chat.focus";
    this.item.text = "$(compass) CodeCompass";
    this.item.tooltip = "CodeCompass";
    this.item.show();
  }

  setState(kind: StatusKind, detail?: string): void {
    switch (kind) {
      case "ready":
        this.item.text = `$(compass) ${detail ?? "CodeCompass"}`;
        this.item.tooltip = "CodeCompass — click to ask a question";
        break;
      case "empty":
        this.item.text = "$(circle-slash) CodeCompass: index workspace";
        this.item.tooltip = "No index yet — click to open Chat and index";
        break;
      case "indexing":
        this.item.text = "$(loading~spin) CodeCompass: indexing…";
        this.item.tooltip = "Indexing the workspace…";
        break;
      case "offline":
        this.item.text = "$(warning) CodeCompass offline";
        this.item.tooltip = detail ?? "The CodeCompass daemon is not running";
        break;
    }
  }

  /** Ask the daemon for status and reflect it in the bar. */
  async refresh(client: CodeCompassClient | undefined): Promise<void> {
    if (!client) {
      this.setState("offline", "Open a workspace folder to use CodeCompass");
      return;
    }
    try {
      const status = await client.status();
      if (status.indexed) {
        const symbols = status.symbols.toLocaleString();
        this.setState("ready", `${symbols} symbols`);
      } else {
        this.setState("empty");
      }
    } catch (error) {
      this.setState("offline", error instanceof Error ? error.message : String(error));
    }
  }

  dispose(): void {
    this.item.dispose();
  }
}
