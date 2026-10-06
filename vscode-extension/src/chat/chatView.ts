import * as vscode from "vscode";

import type { CodeCompassClient } from "../cli/client";
import type { Source } from "../cli/types";
import { openFileAt } from "../util/paths";
import { buildChatHtml } from "./chatPanel";

export interface ChatEntry {
  role: "user" | "assistant";
  text: string;
  error?: boolean;
  sources?: Source[];
}

/**
 * Chat sidebar view: keeps the conversation in extension-land, streams
 * `client.query` tokens into the last assistant entry and pushes the full
 * view state to the webview after every change (single source of truth).
 */
export class ChatViewProvider implements vscode.WebviewViewProvider {
  static readonly viewType = "codecompass.chat";

  private view: vscode.WebviewView | undefined;
  private readonly entries: ChatEntry[] = [];
  private streaming = false;
  private indexed = false;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly getClient: () => CodeCompassClient | undefined,
    private readonly onIndexRequest: () => void
  ) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")],
    };
    view.webview.html = buildChatHtml(view.webview, this.extensionUri);
    view.webview.onDidReceiveMessage((message) => this.onMessage(message));
    void this.refreshStatus();
  }

  /** Re-read index state (after indexing or daemon restarts). */
  async refreshStatus(): Promise<void> {
    const client = this.getClient();
    if (client) {
      try {
        const status = await client.status();
        this.indexed = status.indexed;
      } catch {
        this.indexed = false;
      }
    } else {
      this.indexed = false;
    }
    this.postState();
  }

  private postState(): void {
    void this.view?.webview.postMessage({
      type: "history",
      entries: this.entries,
      streaming: this.streaming,
      indexed: this.indexed,
    });
  }

  private async onMessage(message: { type?: string; text?: string; file?: string; line?: number }): Promise<void> {
    switch (message.type) {
      case "ready":
        this.postState();
        break;
      case "ask":
        await this.ask(String(message.text ?? "").trim());
        break;
      case "openFile": {
        const client = this.getClient();
        if (client && message.file) {
          await openFileAt(client.repoRoot, message.file, message.line ?? 1);
        }
        break;
      }
      case "index":
        this.onIndexRequest();
        break;
    }
  }

  /** Send a question and stream the answer into the conversation. */
  async ask(question: string): Promise<void> {
    if (!question || this.streaming) {
      return;
    }
    this.entries.push({ role: "user", text: question }, { role: "assistant", text: "" });
    const assistant = this.entries[this.entries.length - 1];
    const client = this.getClient();

    if (!client) {
      assistant.error = true;
      assistant.text = "Open a workspace folder to ask CodeCompass questions.";
      this.postState();
      return;
    }

    this.streaming = true;
    this.postState();
    try {
      const result = await client.query(question, 5, (token) => {
        assistant.text += token;
        this.postState();
      });
      assistant.sources = result.sources;
      if (result.count === 0 && !assistant.text) {
        assistant.text = "No relevant context found in the index.";
      }
    } catch (error) {
      assistant.error = true;
      const detail = error instanceof Error ? error.message : String(error);
      assistant.text = assistant.text ? `${assistant.text}\n\n⚠ ${detail}` : `⚠ ${detail}`;
    } finally {
      this.streaming = false;
      this.postState();
      void this.refreshStatus();
    }
  }
}
