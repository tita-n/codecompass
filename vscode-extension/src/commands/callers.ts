import * as vscode from "vscode";

import type { CallEntry } from "../cli/types";
import { openFileAt } from "../util/paths";
import type { GetClient } from "./index";

/** Symbol from the command argument (CodeLens) or the word under the cursor. */
async function resolveSymbol(symbolArg: unknown): Promise<string | undefined> {
  if (typeof symbolArg === "string" && symbolArg.trim()) {
    return symbolArg.trim();
  }
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    void vscode.window.showInformationMessage(
      "CodeCompass: place the cursor on a symbol or invoke this from a CodeLens."
    );
    return undefined;
  }
  const wordRange = editor.document.getWordRangeAtPosition(editor.selection.active);
  if (!wordRange) {
    void vscode.window.showInformationMessage("CodeCompass: place the cursor on a symbol.");
    return undefined;
  }
  return editor.document.getText(wordRange).trim();
}

async function navigateEntries(
  title: string,
  symbol: string,
  entries: CallEntry[],
  repoRoot: string,
  useCallLine: boolean
): Promise<void> {
  const items = entries.map((entry) => {
    const definition = `${entry.file}:${entry.line_start ?? "?"}`;
    const callSite = useCallLine && entry.call_line ? ` · call at line ${entry.call_line}` : "";
    return {
      label: "$(symbol-method) " + entry.name,
      description: definition,
      detail: (entry.kind ?? "") + callSite,
      entry,
    };
  });
  const picked = await vscode.window.showQuickPick(items, {
    title,
    placeHolder: `${entries.length} result(s) for '${symbol}'`,
    matchOnDescription: true,
    matchOnDetail: true,
  });
  if (picked) {
    const jumpLine = useCallLine ? picked.entry.call_line ?? picked.entry.line_start : picked.entry.line_start;
    await openFileAt(repoRoot, picked.entry.file, jumpLine ?? 1);
  }
}

export function registerNavigationCommands(getClient: GetClient): vscode.Disposable[] {
  const findCallers = vscode.commands.registerCommand(
    "codecompass.findCallers",
    async (symbolArg?: unknown) => {
      const client = getClient();
      if (!client) {
        void vscode.window.showErrorMessage("CodeCompass: open a workspace folder first.");
        return;
      }
      const symbol = await resolveSymbol(symbolArg);
      if (!symbol) {
        return;
      }
      try {
        const result = await client.findCallers(symbol);
        if (result.count === 0) {
          void vscode.window.showInformationMessage(`CodeCompass: no callers of '${symbol}'.`);
          return;
        }
        await navigateEntries(
          `Callers of ${symbol}`,
          symbol,
          result.callers,
          client.repoRoot,
          true
        );
      } catch (error) {
        void vscode.window.showErrorMessage(
          `CodeCompass: ${error instanceof Error ? error.message : String(error)}`
        );
      }
    }
  );

  const findCallees = vscode.commands.registerCommand(
    "codecompass.findCallees",
    async (symbolArg?: unknown) => {
      const client = getClient();
      if (!client) {
        void vscode.window.showErrorMessage("CodeCompass: open a workspace folder first.");
        return;
      }
      const symbol = await resolveSymbol(symbolArg);
      if (!symbol) {
        return;
      }
      try {
        const result = await client.findCallees(symbol);
        if (result.count === 0) {
          void vscode.window.showInformationMessage(
            `CodeCompass: '${symbol}' does not call any indexed symbol.`
          );
          return;
        }
        await navigateEntries(
          `Callees of ${symbol}`,
          symbol,
          result.callees,
          client.repoRoot,
          true
        );
      } catch (error) {
        void vscode.window.showErrorMessage(
          `CodeCompass: ${error instanceof Error ? error.message : String(error)}`
        );
      }
    }
  );

  return [findCallers, findCallees];
}
