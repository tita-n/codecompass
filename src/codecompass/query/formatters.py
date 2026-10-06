"""Rich rendering helpers for query CLI output."""

from rich.table import Table
from rich.tree import Tree


def symbol_table(title: str, rows: list[dict]) -> Table:
    """Build a table of symbols with name/file/line/kind/signature columns."""
    table = Table(title=title)
    table.add_column("Symbol", style="bold")
    table.add_column("File", style="cyan")
    table.add_column("Line", style="green")
    table.add_column("Kind", style="magenta")
    table.add_column("Signature")
    for row in rows:
        table.add_row(
            str(row.get("name", "")),
            str(row.get("file", "")),
            str(row.get("line", "") or ""),
            str(row.get("kind", "") or ""),
            str(row.get("signature", "") or ""),
        )
    return table


def coupling_table(rows: list[dict]) -> Table:
    """Build a table of module coupling stats."""
    table = Table(title="Module Coupling")
    table.add_column("File", style="cyan")
    table.add_column("Fan-in", style="green", justify="right")
    table.add_column("Fan-out", style="yellow", justify="right")
    table.add_column("Instability", style="blue", justify="right")
    table.add_column("Coupling", style="magenta", justify="right")
    for row in rows:
        table.add_row(
            str(row.get("file", row.get("path", ""))),
            str(row.get("fan_in", row.get("imported_by", ""))),
            str(row.get("fan_out", row.get("imports", ""))),
            str(row.get("instability", "")),
            str(row.get("coupling", row.get("total", ""))),
        )
    return table


def hotspots_table(rows: list[dict]) -> Table:
    """Build a table of complex functions."""
    table = Table(title="Complexity Hotspots")
    table.add_column("File", style="cyan")
    table.add_column("Lines", style="green")
    table.add_column("Symbol", style="bold")
    table.add_column("Complexity", style="red", justify="right")
    table.add_column("Size", style="yellow", justify="right")
    for row in rows:
        line_start = row.get("line_start", row.get("line", ""))
        line_end = row.get("line_end", row.get("line", ""))
        lines = f"{line_start}-{line_end}" if line_start != "" else ""
        table.add_row(
            str(row.get("file", "")),
            lines,
            str(row.get("name", "")),
            str(row.get("complexity", "") or ""),
            str(row.get("size", "") or ""),
        )
    return table


def circular_tree(cycles: list[list[str]], title: str = "Circular Dependencies") -> Tree:
    """Build a Rich tree with one branch per import cycle."""
    tree = Tree(f"{title} ({len(cycles)} found)")
    for cycle in cycles:
        chain = " → ".join(cycle + ([cycle[0]] if cycle else []))
        tree.add(chain)
    return tree


def dependency_tree(roots: list[dict]) -> Tree:
    """Build a nested Rich tree from dependency tree dicts."""
    tree = Tree("Dependencies")
    for root in roots:
        _add_tree_node(tree, root)
    return tree


def _add_tree_node(parent: Tree, node: dict) -> None:
    location = f"{node['file']}:{node['line']}"
    label = f"[cyan]{node['name']}[/] [dim]({location})[/]"
    child = parent.add(label)
    for sub in node.get("children", []):
        _add_tree_node(child, sub)
