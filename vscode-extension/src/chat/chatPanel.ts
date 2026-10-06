import * as vscode from "vscode";

/** Random nonce for the CSP (all inline scripts must carry it). */
function nonce(): string {
  let text = "";
  const possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  for (let index = 0; index < 32; index++) {
    text += possible.charAt(Math.floor(Math.random() * possible.length));
  }
  return text;
}

const STYLE = `
  :root {
    color-scheme: light dark;
  }
  body {
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    height: 100vh;
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
    color: var(--vscode-editor-foreground);
    background: var(--vscode-editor-background);
  }
  #messages {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
  }
  .empty {
    margin: auto;
    text-align: center;
    max-width: 340px;
    opacity: 0.85;
  }
  .empty h2 { font-size: 1.1em; margin: 12px 0 6px; }
  .empty p { line-height: 1.5; margin: 0 0 14px; }
  .empty ul { text-align: left; padding-left: 18px; line-height: 1.7; margin: 0; }
  .msg { margin-bottom: 14px; }
  .msg .role {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    opacity: 0.6;
    margin-bottom: 4px;
  }
  .msg.user .body {
    background: color-mix(in srgb, var(--vscode-button-background) 16%, transparent);
    border-left: 3px solid var(--vscode-button-background);
    padding: 8px 10px;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .msg.assistant .body { line-height: 1.5; word-break: break-word; }
  .msg.assistant.error .body {
    color: var(--vscode-errorForeground);
    white-space: pre-wrap;
  }
  .msg.assistant .body > :first-child { margin-top: 0; }
  .msg.assistant .body pre {
    background: var(--vscode-textCodeBlock-background);
    padding: 8px;
    overflow-x: auto;
    border-radius: 4px;
  }
  .msg.assistant .body code {
    font-family: var(--vscode-editor-font-family, monospace);
    font-size: 0.95em;
  }
  .cursor::after {
    content: "\\258B";
    animation: blink 1s steps(1) infinite;
    margin-left: 1px;
  }
  @keyframes blink { 50% { opacity: 0; } }
  .sources {
    margin-top: 8px;
    padding-top: 6px;
    border-top: 1px solid var(--vscode-widget-border, rgba(128,128,128,0.35));
  }
  .sources-title {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    opacity: 0.6;
    margin-bottom: 4px;
  }
  button.source {
    display: block;
    width: 100%;
    text-align: left;
    margin: 2px 0;
    padding: 3px 8px;
    border: none;
    border-radius: 3px;
    background: var(--vscode-list-hoverBackground, rgba(128,128,128,0.12));
    color: var(--vscode-textLink-foreground);
    cursor: pointer;
    font-family: var(--vscode-editor-font-family, monospace);
    font-size: 12px;
  }
  button.source:hover { background: var(--vscode-list-activeSelectionBackground); }
  #composer {
    display: flex;
    gap: 6px;
    padding: 10px;
    border-top: 1px solid var(--vscode-widget-border, rgba(128,128,128,0.35));
    background: var(--vscode-sideBar-background, var(--vscode-editor-background));
  }
  #input {
    flex: 1;
    resize: none;
    padding: 6px 8px;
    border: 1px solid var(--vscode-input-border, transparent);
    border-radius: 4px;
    background: var(--vscode-input-background);
    color: var(--vscode-input-foreground);
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
  }
  #input:focus { outline: 1px solid var(--vscode-focusBorder); }
  #send, .empty button {
    padding: 6px 14px;
    border: none;
    border-radius: 4px;
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    cursor: pointer;
  }
  #send:disabled { opacity: 0.5; cursor: default; }
  #send:not(:disabled):hover, .empty button:hover {
    background: var(--vscode-button-hoverBackground);
  }
`;

const BODY = `
  <div id="messages" aria-live="polite">
    <div class="empty" id="empty">
      <h2>CodeCompass</h2>
      <p>Answers grounded in a local index of your code.</p>
      <button id="indexBtn">Index Workspace</button>
    </div>
  </div>
  <form id="composer" autocomplete="off">
    <textarea id="input" rows="2"
      placeholder="Ask about your codebase… (Enter to send, Shift+Enter for a newline)"></textarea>
    <button id="send" type="submit">Send</button>
  </form>
`;

const SCRIPT = `
  const vscode = acquireVsCodeApi();
  const state = { entries: [], streaming: false, indexed: false };

  const messagesEl = document.getElementById("messages");
  const inputEl = document.getElementById("input");
  const sendEl = document.getElementById("send");

  function esc(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function welcomeHtml() {
    if (!state.indexed) {
      return (
        '<div class="empty"><h2>Index your workspace</h2>' +
        "<p>CodeCompass answers from a local index of your code. " +
        "Nothing is sent anywhere until you ask a question.</p>" +
        '<button id="indexBtn">Index Workspace</button></div>'
      );
    }
    return (
      '<div class="empty"><h2>Ask a question</h2>' +
      "<p>The answer streams in with links back to the exact lines it used.</p>" +
      "<ul><li>How does checkout work?</li>" +
      "<li>Who calls validate_payment?</li>" +
      "<li>Where should I add a new payment provider?</li></ul></div>"
    );
  }

  function markdown(text) {
    if (typeof marked !== "undefined" && marked.parse) {
      return marked.parse(text);
    }
    return "<pre>" + esc(text) + "</pre>";
  }

  function sourcesHtml(entry) {
    if (!entry.sources || entry.sources.length === 0) {
      return "";
    }
    const rows = entry.sources
      .map(function (source) {
        const label = (source.file || "?") + ":" + (source.line_start || "?");
        const symbol = source.symbol_name ? " · " + source.symbol_name : "";
        return (
          '<button class="source" data-file="' + esc(source.file || "") +
          '" data-line="' + (source.line_start || 1) + '">' +
          esc(label + symbol) + "</button>"
        );
      })
      .join("");
    return (
      '<div class="sources"><div class="sources-title">Sources (' +
      entry.sources.length + ")</div>" + rows + "</div>"
    );
  }

  function entryHtml(entry, index) {
    if (entry.role === "user") {
      return (
        '<div class="msg user"><div class="role">You</div>' +
        '<div class="body">' + esc(entry.text) + "</div></div>"
      );
    }
    const streamingThis = state.streaming && index === state.entries.length - 1;
    const body = entry.text
      ? markdown(entry.text)
      : streamingThis
        ? '<span class="cursor"></span>'
        : "";
    const classes = "msg assistant" + (entry.error ? " error" : "");
    return (
      '<div class="' + classes + '"><div class="role">CodeCompass</div>' +
      '<div class="body">' + body + "</div>" +
      (streamingThis && entry.text ? '<span class="cursor"></span>' : "") +
      sourcesHtml(entry) + "</div>"
    );
  }

  function render() {
    if (state.entries.length === 0) {
      messagesEl.innerHTML = welcomeHtml();
    } else {
      messagesEl.innerHTML = state.entries.map(entryHtml).join("");
    }
    sendEl.disabled = state.streaming;
    if (state.streaming) {
      inputEl.placeholder = "Waiting for the answer…";
    } else {
      inputEl.placeholder = "Ask about your codebase… (Enter to send, Shift+Enter for a newline)";
    }
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  window.addEventListener("message", function (event) {
    const message = event.data;
    if (!message || message.type !== "history") {
      return;
    }
    state.entries = message.entries || [];
    state.streaming = !!message.streaming;
    state.indexed = !!message.indexed;
    render();
  });

  document.getElementById("composer").addEventListener("submit", function (event) {
    event.preventDefault();
    const text = inputEl.value.trim();
    if (!text || state.streaming) {
      return;
    }
    inputEl.value = "";
    vscode.postMessage({ type: "ask", text: text });
  });

  inputEl.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      document.getElementById("composer").requestSubmit();
    }
  });

  messagesEl.addEventListener("click", function (event) {
    const target = event.target;
    if (!target) {
      return;
    }
    if (target.id === "indexBtn") {
      vscode.postMessage({ type: "index" });
      return;
    }
    if (target.classList.contains("source")) {
      vscode.postMessage({
        type: "openFile",
        file: target.getAttribute("data-file"),
        line: Number(target.getAttribute("data-line")),
      });
    }
  });

  render();
  vscode.postMessage({ type: "ready" });
`;

/**
 * Full HTML document for the Chat webview: strict CSP (nonce + webview origin),
 * local-only assets (vendored marked.min.js — no CDN) and a single inline
 * script that renders the streamed conversation.
 */
export function buildChatHtml(webview: vscode.Webview, extensionUri: vscode.Uri): string {
  const scriptNonce = nonce();
  const markedUri = webview.asWebviewUri(
    vscode.Uri.joinPath(extensionUri, "media", "vendor", "marked.min.js")
  );
  const csp = [
    "default-src 'none'",
    `img-src ${webview.cspSource} data:`,
    `style-src ${webview.cspSource} 'nonce-${scriptNonce}'`,
    `script-src ${webview.cspSource} 'nonce-${scriptNonce}'`,
    `font-src ${webview.cspSource}`,
  ].join("; ");

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="${csp}">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CodeCompass Chat</title>
  <style nonce="${scriptNonce}">${STYLE}</style>
</head>
<body>
${BODY}
  <script nonce="${scriptNonce}" src="${markedUri}"></script>
  <script nonce="${scriptNonce}">
${SCRIPT}
  </script>
</body>
</html>`;
}
