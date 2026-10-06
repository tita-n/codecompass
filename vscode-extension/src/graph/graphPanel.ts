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
  :root { color-scheme: light dark; }
  body {
    margin: 0;
    display: flex;
    flex-direction: column;
    height: 100vh;
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
    color: var(--vscode-editor-foreground);
    background: var(--vscode-editor-background);
  }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border-bottom: 1px solid var(--vscode-widget-border, rgba(128,128,128,0.35));
    background: var(--vscode-sideBar-background, var(--vscode-editor-background));
    flex-wrap: wrap;
  }
  .toolbar label { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
  .toolbar input[type="range"] { width: 90px; accent-color: var(--vscode-button-background); }
  #root {
    font-family: var(--vscode-editor-font-family, monospace);
    font-size: 12px;
    opacity: 0.85;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  #counts { margin-left: auto; font-size: 11px; opacity: 0.6; }
  #graph { flex: 1; }
  .placeholder {
    margin: auto;
    text-align: center;
    max-width: 340px;
    opacity: 0.85;
    padding: 20px;
  }
  .placeholder h2 { font-size: 1.1em; margin: 0 0 6px; }
  .placeholder p { line-height: 1.5; margin: 0; }
`;

const BODY = `
  <div class="toolbar">
    <label>Depth
      <input type="range" id="depth" min="1" max="6" value="3">
      <span id="depthValue">3</span>
    </label>
    <span id="root"></span>
    <span id="counts"></span>
  </div>
  <div id="graph">
    <div class="placeholder" id="placeholder">
      <h2>No graph yet</h2>
      <p>Right-click a symbol and choose <strong>CodeCompass: Show Dependencies</strong>.</p>
    </div>
  </div>
`;

const SCRIPT = `
  const vscode = acquireVsCodeApi();
  const graphEl = document.getElementById("graph");
  const depthInput = document.getElementById("depth");
  const depthValue = document.getElementById("depthValue");

  let network = null;
  let nodeMeta = {};
  let lastRoot = "";
  let depthTimer = null;

  function theme(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name);
    return value.trim() || fallback;
  }

  function shortFile(file) {
    const parts = String(file).split("/");
    return parts.length <= 2 ? parts.join("/") : "…" + parts.slice(-2).join("/");
  }

  function colorFor(kind) {
    const focus = theme("--vscode-focusBorder", "#4a9eff");
    const warning = theme("--vscode-editorWarning-foreground", "#cca700");
    const muted = theme("--vscode-descriptionForeground", "#888888");
    if (kind === "class") {
      return { background: warning, border: warning };
    }
    if (kind === "method") {
      return { background: muted, border: muted };
    }
    return { background: focus, border: focus };
  }

  function options() {
    const fg = theme("--vscode-editor-foreground", "#cccccc");
    const border = theme("--vscode-widget-border", "rgba(128,128,128,0.5)");
    return {
      nodes: {
        shape: "box",
        font: { color: fg, face: "monospace", size: 13 },
        margin: 8,
        borderWidth: 1.5,
      },
      edges: {
        color: { color: border, highlight: fg },
        arrows: { to: { enabled: true, scaleFactor: 0.6 } },
        font: { color: fg, size: 10, align: "middle" },
        smooth: true,
      },
      physics: {
        solver: "forceAtlas2Based",
        forceAtlas2Based: { gravitationalConstant: -45, springLength: 130 },
        stabilization: { iterations: 250 },
      },
      interaction: { hover: true, tooltipDelay: 120 },
    };
  }

  function renderGraph(data) {
    if (typeof vis === "undefined" || !vis.DataSet || !vis.Network) {
      graphEl.innerHTML =
        '<div class="placeholder"><h2>Graph library failed to load</h2>' +
        "<p>Reinstall the extension so media/vendor assets are restored.</p></div>";
      return;
    }
    lastRoot = data.root;
    nodeMeta = {};
    const nodes = new vis.DataSet(
      data.nodes.map(function (node) {
        nodeMeta[node.id] = node;
        return {
          id: node.id,
          label: node.name + "\\n" + shortFile(node.file) + ":" + (node.line_start || "?"),
          title: (node.signature || node.name) + "\\n" + node.file + ":" + (node.line_start || ""),
          color: colorFor(node.kind),
        };
      })
    );
    const edges = new vis.DataSet(
      data.edges.map(function (edge, index) {
        return {
          id: index,
          from: edge.from,
          to: edge.to,
          arrows: "to",
          title: edge.line ? "call at line " + edge.line : "",
        };
      })
    );
    graphEl.innerHTML = "";
    network = new vis.Network(graphEl, { nodes: nodes, edges: edges }, options());
    network.on("click", function (params) {
      if (params.nodes.length === 0) {
        return;
      }
      const node = nodeMeta[params.nodes[0]];
      if (node) {
        vscode.postMessage({ type: "openFile", file: node.file, line: node.line_start || 1 });
      }
    });
    document.getElementById("root").textContent = data.root;
    document.getElementById("counts").textContent =
      data.node_count + " symbols · " + data.edge_count + " calls";
  }

  depthInput.addEventListener("input", function () {
    depthValue.textContent = depthInput.value;
    if (depthTimer) {
      clearTimeout(depthTimer);
    }
    depthTimer = setTimeout(function () {
      if (lastRoot) {
        vscode.postMessage({ type: "depth", depth: Number(depthInput.value) });
      }
    }, 300);
  });

  window.addEventListener("message", function (event) {
    const message = event.data;
    if (message && message.type === "graph") {
      renderGraph(message.data);
    }
  });

  vscode.postMessage({ type: "ready" });
`;

/**
 * Full HTML document for the Graph webview: vendored vis-network (offline,
 * no CDN) plus one inline script that renders the trace node/edge payload.
 */
export function buildGraphHtml(webview: vscode.Webview, extensionUri: vscode.Uri): string {
  const scriptNonce = nonce();
  const vendor = (name: string) =>
    webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, "media", "vendor", name)).toString();
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
  <title>CodeCompass Graph</title>
  <style nonce="${scriptNonce}">${STYLE}</style>
</head>
<body>
${BODY}
  <script nonce="${scriptNonce}" src="${vendor("vis-network.min.js")}"></script>
  <script nonce="${scriptNonce}">
${SCRIPT}
  </script>
</body>
</html>`;
}
