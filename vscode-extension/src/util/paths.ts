import * as path from "path";

import * as vscode from "vscode";

/** First workspace folder's filesystem path, if any. */
export function getRepoRoot(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

/** Absolute path -> repo-relative POSIX path (rejects paths outside the repo). */
export function toRepoRelative(repoRoot: string, fsPath: string): string {
  const relative = path.relative(repoRoot, fsPath);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`${fsPath} is outside the workspace folder`);
  }
  return relative.split(path.sep).join("/");
}

/** Open `relative` (POSIX, repo-rooted) at `line` and reveal it. */
export async function openFileAt(
  repoRoot: string,
  relative: string,
  line?: number | null
): Promise<void> {
  const segments = relative.split("/").filter((segment) => segment.length > 0);
  const uri = vscode.Uri.joinPath(vscode.Uri.file(repoRoot), ...segments);
  const document = await vscode.workspace.openTextDocument(uri);
  const editor = await vscode.window.showTextDocument(document);
  const lineNumber = Math.max(0, (line ?? 1) - 1);
  const clamped = Math.min(lineNumber, Math.max(0, document.lineCount - 1));
  const position = new vscode.Position(clamped, 0);
  editor.selection = new vscode.Selection(position, position);
  editor.revealRange(new vscode.Range(position, position), vscode.TextEditorRevealType.InCenter);
}

/** Lowercase file extension -> language hint for prompts (e.g. "py" -> "python"). */
export function languageFromPath(fsPath: string): string {
  const ext = path.extname(fsPath).slice(1).toLowerCase();
  const aliases: Record<string, string> = {
    py: "python",
    js: "javascript",
    ts: "typescript",
    tsx: "typescript",
    jsx: "javascript",
    rb: "ruby",
    go: "go",
    rs: "rust",
    java: "java",
    c: "c",
    h: "c",
    cpp: "cpp",
    hpp: "cpp",
    cs: "csharp",
    php: "php",
    kt: "kotlin",
    swift: "swift",
  };
  return aliases[ext] || ext || "text";
}
