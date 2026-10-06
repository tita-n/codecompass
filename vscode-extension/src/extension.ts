import * as vscode from "vscode";

import { CodeCompassClient } from "./cli/client";
import { ChatViewProvider } from "./chat/chatView";
import { registerNavigationCommands } from "./commands/callers";
import { registerDependencyCommands } from "./commands/dependencies";
import { registerReviewCommands } from "./commands/explain";
import { GetClient, registerIndexCommand, runIndex } from "./commands/index";
import { CallerLensProvider } from "./codelens/callerLens";
import { GraphViewProvider } from "./graph/graphView";
import { StatusBarController } from "./status/statusBar";
import { createLogger, Logger } from "./util/logger";
import { getRepoRoot } from "./util/paths";

let client: CodeCompassClient | undefined;

/** Spawn (or return) the daemon for the current workspace + settings. */
function makeClient(logger: Logger, onExit: (code: number | null) => void): CodeCompassClient | undefined {
  const repoRoot = getRepoRoot();
  if (!repoRoot) {
    logger.warn("no workspace folder open; daemon not started");
    return undefined;
  }
  const config = vscode.workspace.getConfiguration("codecompass");
  const instance = new CodeCompassClient({
    pythonPath: config.get<string>("pythonPath", "python3"),
    repoRoot,
    llmProvider: config.get<string>("llmProvider", "ollama"),
    onStderr: (line) => {
      logger.raw(line);
      if (line.includes("No module named") && line.includes("codecompass")) {
        void vscode.window.showErrorMessage(
          "CodeCompass: the configured Python interpreter does not have the codecompass package installed. " +
            "Set 'CodeCompass: Python Path' to the right interpreter.",
          "Open Settings"
        ).then((action) => {
          if (action === "Open Settings") {
            void vscode.commands.executeCommand(
              "workbench.action.openSettings",
              "codecompass.pythonPath"
            );
          }
        });
      }
    },
    onExit,
  });
  instance.start();
  return instance;
}

export function activate(context: vscode.ExtensionContext): void {
  const logger = createLogger(context);
  const statusBar = new StatusBarController();
  context.subscriptions.push(statusBar);

  const handleExit = (code: number | null): void => {
    logger.warn(`daemon exited with code ${code ?? "unknown"}`);
    statusBar.setState("offline", `daemon exited (code ${code ?? "?"})`);
    void vscode.window
      .showErrorMessage(
        `CodeCompass daemon exited (code ${code ?? "unknown"}). See the CodeCompass output channel.`,
        "Reload Window"
      )
      .then((action) => {
        if (action === "Reload Window") {
          void vscode.commands.executeCommand("workbench.action.reloadWindow");
        }
      });
  };

  client = makeClient(logger, handleExit);
  const getClient: GetClient = () => client;

  const onIndexDone = (): void => {
    void statusBar.refresh(client);
    void chatView.refreshStatus();
  };

  // Views ------------------------------------------------------------------
  const chatView = new ChatViewProvider(context.extensionUri, getClient, () =>
    runIndex(client, statusBar, logger, onIndexDone)
  );
  const graphView = new GraphViewProvider(context.extensionUri, getClient);
  const retain = { webviewOptions: { retainContextWhenHidden: true } };
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(ChatViewProvider.viewType, chatView, retain),
    vscode.window.registerWebviewViewProvider(GraphViewProvider.viewType, graphView, retain)
  );

  // Commands ---------------------------------------------------------------
  context.subscriptions.push(
    registerIndexCommand(getClient, statusBar, logger, onIndexDone),
    vscode.commands.registerCommand("codecompass.query", () =>
      vscode.commands.executeCommand("codecompass.chat.focus")
    ),
    ...registerReviewCommands(getClient),
    ...registerNavigationCommands(getClient),
    ...registerDependencyCommands(getClient, graphView)
  );

  // CodeLens ---------------------------------------------------------------
  const lensProvider = new CallerLensProvider(getClient);
  context.subscriptions.push(
    lensProvider,
    vscode.languages.registerCodeLensProvider("*", lensProvider)
  );

  // Settings changes -------------------------------------------------------
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (
        event.affectsConfiguration("codecompass.pythonPath") ||
        event.affectsConfiguration("codecompass.llmProvider")
      ) {
        logger.info("daemon-relevant setting changed; restarting daemon");
        client?.restart();
        void statusBar.refresh(client);
        void chatView.refreshStatus();
      }
    })
  );

  // Initial state ----------------------------------------------------------
  void statusBar.refresh(client);
  void chatView.refreshStatus();

  const autoIndex = vscode.workspace.getConfiguration("codecompass").get<boolean>("autoIndex", false);
  if (client && autoIndex) {
    void (async () => {
      try {
        const status = await client!.status();
        if (!status.indexed) {
          logger.info("autoIndex: no index found, indexing now");
          await runIndex(client, statusBar, logger, onIndexDone);
        }
      } catch (error) {
        logger.warn(`autoIndex skipped: ${error instanceof Error ? error.message : String(error)}`);
      }
    })();
  }

  logger.info("CodeCompass activated");
}

export function deactivate(): void {
  client?.dispose();
  client = undefined;
}
