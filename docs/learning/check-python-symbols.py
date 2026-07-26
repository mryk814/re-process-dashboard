from __future__ import annotations

import ast
import base64
import json
import sys


def top_level_symbols(source: str) -> dict[str, int]:
    tree = ast.parse(source)
    symbols: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols[node.name] = node.lineno
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    symbols[target.id] = target.lineno
    return symbols


source = base64.b64decode(sys.stdin.read()).decode("utf-8")
print(json.dumps(top_level_symbols(source), ensure_ascii=False))
