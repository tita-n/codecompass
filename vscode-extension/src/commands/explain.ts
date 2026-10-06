import * as vscode from "vscode";

import type { CodeCompassClient } from "../cli/client";
import { toRepoRelative } from "../util/paths";
import type { GetClient } from "./index";

type ReviewMode = "explain" | "simplify";

/**
 * Resolve the target range: the selection, or - when the cursor sits on a
 * word - the full indexed body of that symbol.
 */
async function resolveRange(
  client: CodeCompassClient,
  editor: vscode.TextEditor
): Promise<{ start: number; end: number } | undefined> {
  const { document, selection } = editor;
  if (!selection.isEmpty) {
    const startLine = Math.min(selection.start.line, selection.end.line) + 1;
    const endLine = selection.end.character > 0 ? selection.end.line + 1 : selection.end.line;
    return { start: startLine, end: Math.max(startLine, endLine) };
  }

  const wordRange = document.getWordRangeAtPosition(selection.active);
  if (!wordRange) {
    void vscode.window.showInformationMessage(
      "CodeCompass: select some code or place the cursor on a symbol."
    );
    return undefined;
  }
  const word = document.getText(wordRange).trim();
  const found = await client.findSymbol(word);
  if (!found.found) {
    void vscode.window.showInformationMessage(
      `CodeCompass: symbol '${word}' is not in the index (try Index Workspace).`
    );
    return undefined;
  }
  const relative = toRepoRelative(client.repoRoot, document.uri.fsPath);
  const match =
    found.symbols.find((symbol) => symbol.file === relative) ?? found.symbols[0];
  const start = match.line_start ?? 1;
  const end = match.line_end ?? start;

  const startLine = new vscode.Position(start - 1, 0);
  const endLine = new vscode.Position(end - 1, 0);
  editor.selection = new vscode.Selection(endLine, startLine);
  editor.revealRange(new vscode.Range(startLine, endLine), vscode.TextEditorRevealType.InCenter);
  return { start, end };
}

/** Stream `mode` output into an untitled document beside the editor. */
async function runReview(mode: ReviewMode, client: CodeCompassClient | undefined): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    void vscode.window.showInformationMessage("CodeCompass: open a file first.");
    return;
  }
  if (!client) {
    void vscode.window.showErrorMessage("CodeCompass: open a workspace folder first.");
    return;
  }

  let range: { start: number; end: number } | undefined;
  try {
    range = await resolveRange(client, editor);
  } catch (error) {
    void vscode.window.showErrorMessage(
      `CodeCompass: ${error instanceof Error ? error.message : String(error)}`
    );
    return;
  }
  if (!range) {
    return;
  }

  const relative = toRepoRelative(client.repoRoot, editor.document.uri.fsPath);
  const title = mode === "explain" ? "Explain" : "Simplify";
  const header = `# ${title}: ${relative}:${range.start}-${range.end}\n\n`;
  if (mode === "simplify") {
    void vscode.window.showInformationMessage(
      "CodeCompass: simplify is a read-only preview — no files are modified."
    );
  }

  const document = await vscode.workspace.openTextDocument({ content: header });
  const target = await vscode.window.showTextDocument(document, vscode.ViewColumn.Beside);

  // Serialize token appends so positions stay ordered.
  let queue: Promise<boolean> = Promise.resolve(true);
  const append = (token: string): void => {
    queue = queue.then(() => {
      const position = document.positionAt(document.getText().length);
      const edit = new vscode.WorkspaceEdit();
      edit.insert(document.uri, position, token);
      return vscode.workspace.applyEdit(edit);
    });
  };

  try {
    const stream =
      mode === "explain"
        ? client.explain.bind(client)
        : client.simplify.bind(client);
    await stream(relative, range.start, range.end, append);
    await queue;
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    append(`\n\n⚠ ${detail}`);
    await queue;
    void vscode.window.showErrorMessage(`CodeCompass: ${detail}`);
  }
  // Keep focus on the streamed document.
  void target;
}

export function registerReviewCommands(getClient: GetClient): vscode.Disposable[] {
  return [
    vscode.commands.registerCommand("codecompass.explainSelection", () =>
      runReview("explain", getClient())
    ),
    vscode.commands.registerCommand("codecompass.simplify", () =>
      runReview("simplify", getClient())
    ),
  ];
}
