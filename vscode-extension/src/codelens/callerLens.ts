import * as vscode from "vscode";

import type { GetClient } from "../commands/index";

/** Languages where we can find definition symbols via the built-in providers. */
const SUPPORTED_LANGUAGES = new Set([
  "python",
  "typescript",
  "javascript",
  "typescriptreact",
  "javascriptreact",
  "go",
  "rust",
  "java",
  "c",
  "cpp",
  "csharp",
  "php",
  "ruby",
  "kotlin",
  "swift",
]);

/** Cap per file so a huge file cannot flood the daemon with requests. */
const MAX_SYMBOLS_PER_FILE = 40;

function flatten(symbols: vscode.DocumentSymbol[]): vscode.DocumentSymbol[] {
  const flat: vscode.DocumentSymbol[] = [];
  const walk = (list: vscode.DocumentSymbol[]): void => {
    for (const symbol of list) {
      flat.push(symbol);
      if (symbol.children.length > 0) {
        walk(symbol.children);
      }
    }
  };
  walk(symbols);
  return flat;
}

/**
 * "'N callers' CodeLens" above function/method definitions. Results are
 * cached per document version; `showCodeLens` is honoured live, so toggling
 * the setting takes effect without a reload.
 */
export class CallerLensProvider implements vscode.CodeLensProvider {
  private readonly cache = new Map<string, { version: number; lenses: vscode.CodeLens[] }>();
  private readonly emitter = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this.emitter.event;

  constructor(private readonly getClient: GetClient) {}

  async provideCodeLenses(document: vscode.TextDocument): Promise<vscode.CodeLens[]> {
    const enabled = vscode.workspace
      .getConfiguration("codecompass")
      .get<boolean>("showCodeLens", true);
    const client = this.getClient();
    if (!enabled || !client || !SUPPORTED_LANGUAGES.has(document.languageId)) {
      return [];
    }

    const cacheKey = document.uri.toString();
    const cached = this.cache.get(cacheKey);
    if (cached && cached.version === document.version) {
      return cached.lenses;
    }

    try {
      const symbols = await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
        "vscode.executeDocumentSymbolProvider",
        document.uri
      );
      if (!symbols) {
        return [];
      }
      const definitions = flatten(symbols)
        .filter(
          (symbol) =>
            symbol.kind === vscode.SymbolKind.Function ||
            symbol.kind === vscode.SymbolKind.Method
        )
        .slice(0, MAX_SYMBOLS_PER_FILE);

      const lenses: vscode.CodeLens[] = [];
      for (const definition of definitions) {
        const result = await client.findCallers(definition.name);
        if (result.count > 0) {
          lenses.push(
            new vscode.CodeLens(definition.range, {
              title: `${result.count} caller${result.count === 1 ? "" : "s"}`,
              command: "codecompass.findCallers",
              arguments: [definition.name],
              tooltip: `Find callers of ${definition.name}`,
            })
          );
        }
      }
      this.cache.set(cacheKey, { version: document.version, lenses });
      return lenses;
    } catch {
      // Never break the editor: log-and-return empty.
      return [];
    }
  }

  dispose(): void {
    this.emitter.dispose();
    this.cache.clear();
  }
}
