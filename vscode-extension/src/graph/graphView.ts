import * as vscode from "vscode";

import type { CodeCompassClient } from "../cli/client";
import type { TraceResult } from "../cli/types";
import { openFileAt } from "../util/paths";
import { buildGraphHtml } from "./graphPanel";

/**
 * Graph sidebar view: renders the latest `trace` payload with vis-network.
 * The depth slider re-traces the same root symbol through the daemon, so the
 * view can be widened/narrowed without going back to the editor.
 */
export class GraphViewProvider implements vscode.WebviewViewProvider {
  static readonly viewType = "codecompass.graph";

  private view: vscode.WebviewView | undefined;
  private lastData: TraceResult | undefined;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly getClient: () => CodeCompassClient | undefined
  ) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")],
    };
    view.webview.html = buildGraphHtml(view.webview, this.extensionUri);
    view.webview.onDidReceiveMessage((message) => this.onMessage(message));
  }

  /** Focus the view (creating it if needed), then push the current graph. */
  async show(data: TraceResult): Promise<void> {
    this.lastData = data;
    await vscode.commands.executeCommand(`${GraphViewProvider.viewType}.focus`);
    this.post();
  }

  private post(): void {
    if (this.lastData) {
      void this.view?.webview.postMessage({ type: "graph", data: this.lastData });
    }
  }

  private async onMessage(
    message: { type?: string; file?: string; line?: number; depth?: number }
  ): Promise<void> {
    const client = this.getClient();
    switch (message.type) {
      case "ready":
        this.post();
        break;
      case "openFile":
        if (client && message.file) {
          await openFileAt(client.repoRoot, message.file, message.line ?? 1);
        }
        break;
      case "depth":
        if (client && this.lastData) {
          try {
            const depth = Math.max(1, Math.min(6, Number(message.depth) || 3));
            const data = await client.trace(this.lastData.root, depth);
            this.lastData = data;
            this.post();
          } catch (error) {
            void vscode.window.showErrorMessage(
              `CodeCompass: ${error instanceof Error ? error.message : String(error)}`
            );
          }
        }
        break;
    }
  }
}
