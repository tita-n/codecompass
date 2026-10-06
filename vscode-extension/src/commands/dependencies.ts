import * as vscode from "vscode";

import { openFileAt } from "../util/paths";
import type { GraphViewProvider } from "../graph/graphView";
import type { GetClient } from "./index";

async function symbolFromArgOrCursor(symbolArg: unknown): Promise<string | undefined> {
  if (typeof symbolArg === "string" && symbolArg.trim()) {
    return symbolArg.trim();
  }
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    void vscode.window.showInformationMessage("CodeCompass: place the cursor on a symbol.");
    return undefined;
  }
  const wordRange = editor.document.getWordRangeAtPosition(editor.selection.active);
  if (!wordRange) {
    void vscode.window.showInformationMessage("CodeCompass: place the cursor on a symbol.");
    return undefined;
  }
  return editor.document.getText(wordRange).trim();
}

/** Show the callee graph of a symbol in the Graph view. */
export function registerDependencyCommands(
  getClient: GetClient,
  graphView: GraphViewProvider
): vscode.Disposable[] {
  const showDependencies = vscode.commands.registerCommand(
    "codecompass.showDependencies",
    async (symbolArg?: unknown) => {
      const client = getClient();
      if (!client) {
        void vscode.window.showErrorMessage("CodeCompass: open a workspace folder first.");
        return;
      }
      const symbol = await symbolFromArgOrCursor(symbolArg);
      if (!symbol) {
        return;
      }
      try {
        const data = await client.trace(symbol, 3);
        if (!data.found) {
          void vscode.window.showInformationMessage(
            `CodeCompass: '${symbol}' is not in the index (try Index Workspace).`
          );
          return;
        }
        await graphView.show(data);
      } catch (error) {
        void vscode.window.showErrorMessage(
          `CodeCompass: ${error instanceof Error ? error.message : String(error)}`
        );
      }
    }
  );

  const impactAnalysis = vscode.commands.registerCommand(
    "codecompass.impactAnalysis",
    async (symbolArg?: unknown) => {
      const client = getClient();
      if (!client) {
        void vscode.window.showErrorMessage("CodeCompass: open a workspace folder first.");
        return;
      }
      const symbol = await symbolFromArgOrCursor(symbolArg);
      if (!symbol) {
        return;
      }
      try {
        const result = await client.impact(symbol);
        if (!result.found) {
          void vscode.window.showInformationMessage(
            `CodeCompass: '${symbol}' is not in the index (try Index Workspace).`
          );
          return;
        }
        const items = result.affected_files.map((file) => ({
          label: `$(file-code) ${file}`,
          description: file === result.symbol ? "the symbol itself" : undefined,
          file,
        }));
        const picked = await vscode.window.showQuickPick(items, {
          title: `${result.risk} risk · ${result.direct_callers} direct · ${result.total_affected} affected`,
          placeHolder: result.reasoning,
          matchOnDescription: true,
        });
        if (picked) {
          await openFileAt(client.repoRoot, picked.file, 1);
        }
      } catch (error) {
        void vscode.window.showErrorMessage(
          `CodeCompass: ${error instanceof Error ? error.message : String(error)}`
        );
      }
    }
  );

  return [showDependencies, impactAnalysis];
}
