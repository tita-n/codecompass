import * as vscode from "vscode";

import type { CodeCompassClient } from "../cli/client";
import type { Logger } from "../util/logger";
import type { StatusBarController } from "../status/statusBar";

export type GetClient = () => CodeCompassClient | undefined;

/**
 * Index the workspace with progress reporting. Used by the command, the
 * Chat view's "Index Workspace" button and the `autoIndex` setting.
 */
export async function runIndex(
  client: CodeCompassClient | undefined,
  statusBar: StatusBarController,
  logger: Logger,
  onDone?: () => void
): Promise<void> {
  if (!client) {
    void vscode.window.showErrorMessage("CodeCompass: open a workspace folder first.");
    return;
  }
  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: `CodeCompass: indexing ${client.repoRoot}…`,
      cancellable: false,
    },
    async () => {
      statusBar.setState("indexing");
      try {
        const stats = await client.index(client.repoRoot, false);
        logger.info(
          `indexed ${stats.repo_root}: files=${stats.files} symbols=${stats.symbols} chunks=${stats.chunks} duration=${stats.duration_seconds}s`
        );
        const action = await vscode.window.showInformationMessage(
          `CodeCompass indexed ${stats.files} files · ${stats.symbols} symbols · ${stats.chunks} chunks`,
          "Ask a Question"
        );
        if (action === "Ask a Question") {
          await vscode.commands.executeCommand("codecompass.chat.focus");
        }
        onDone?.();
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        logger.error(`indexing failed: ${detail}`);
        statusBar.setState("offline", detail);
        void vscode.window.showErrorMessage(`CodeCompass indexing failed: ${detail}`);
      }
    }
  );
}

export function registerIndexCommand(
  getClient: GetClient,
  statusBar: StatusBarController,
  logger: Logger,
  onDone?: () => void
): vscode.Disposable {
  return vscode.commands.registerCommand("codecompass.index", () =>
    runIndex(getClient(), statusBar, logger, onDone)
  );
}
