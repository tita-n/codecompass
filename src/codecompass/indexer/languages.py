"""Language configuration for Tree-sitter parsing."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from tree_sitter import Parser

# Language definitions
LANGUAGE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".php": "php",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".cs": "c_sharp",
}

# Language-specific ignore patterns
IGNORE_DIRS = {
    "node_modules",
    ".git",
    "dist",
    "build",
    "target",
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
    ".idea",
    ".vscode",
}


@dataclass
class LanguageConfig:
    """Configuration for a specific language."""

    name: str
    extensions: list[str]
    parser: Parser | None = None
    grammar_path: Path | None = None

    def get_parser(self) -> Parser:
        """Get or create the Tree-sitter parser for this language."""
        if self.parser is not None:
            return self.parser
        raise RuntimeError(f"Parser for {self.name} not initialized")


# Language definitions
LANGUAGES: dict[str, LanguageConfig] = {
    "python": LanguageConfig(name="python", extensions=[".py"]),
    "javascript": LanguageConfig(name="javascript", extensions=[".js", ".jsx"]),
    "typescript": LanguageConfig(name="typescript", extensions=[".ts", ".tsx"]),
    "java": LanguageConfig(name="java", extensions=[".java"]),
    "go": LanguageConfig(name="go", extensions=[".go"]),
    "rust": LanguageConfig(name="rust", extensions=[".rs"]),
    "php": LanguageConfig(name="php", extensions=[".php"]),
    "cpp": LanguageConfig(name="cpp", extensions=[".cpp", ".hpp", ".cc", ".cxx"]),
    "c_sharp": LanguageConfig(name="c_sharp", extensions=[".cs"]),
}


def get_language_for_file(file_path: Path) -> LanguageConfig | None:
    """Determine the language for a file based on its extension."""
    for ext, lang_name in LANGUAGE_EXTENSIONS.items():
        if str(file_path).endswith(ext):
            return LANGUAGES.get(lang_name)
    return None


@lru_cache(maxsize=1)
def _config_exclude_dirs() -> frozenset[str]:
    """Load user-configured exclusion directories (cached per process)."""
    try:
        from codecompass.utils.config import get_config

        return frozenset(get_config().indexing.exclude_dirs)
    except Exception:
        return frozenset()


def is_ignored_path(file_path: Path, repo_root: Path) -> bool:
    """Check if a path should be ignored during indexing."""
    try:
        relative_path = file_path.relative_to(repo_root)
    except ValueError:
        # Path is outside the repository root; treat as not ignored.
        return False

    parts = set(relative_path.parts)

    if parts & IGNORE_DIRS:
        return True

    # Honor exclusions from the user configuration (e.g. config.yaml).
    return bool(parts & _config_exclude_dirs())


def is_supported_file(file_path: Path) -> bool:
    """Check if a file is supported for indexing."""
    return get_language_for_file(file_path) is not None
