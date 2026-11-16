"""Measure an immutable Git tree; no packages or runtime imports are required."""

import ast
import collections
import io
import json
import subprocess
import sys
import tarfile
import tomllib

revision = sys.argv[1]
archive = subprocess.check_output(["git", "archive", revision])
with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
    files = {
        member.name: tree.extractfile(member).read().decode("utf-8", errors="replace")
        for member in tree.getmembers()
        if member.isfile()
    }

groups = {
    "core": "packages/decepticon-core/decepticon_core/",
    "framework": "packages/decepticon/decepticon/",
    "sandbox": "packages/decepticon/decepticon_sandbox/",
    "sdk": "packages/decepticon-sdk/decepticon_sdk/",
    "launcher": "clients/launcher/",
    "cli": "clients/cli/src/",
    "streaming": "clients/shared/streaming/src/",
    "telemetry_gateway": "telemetry-gateway/src/",
    "benchmark": "benchmark/",
    "provider_config": "config/",
}
extensions = (".py", ".go", ".ts", ".tsx", ".js", ".rs")


def source(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        path.endswith(extensions)
        and "/tests/" not in path
        and not name.startswith("test_")
        and not name.endswith(("_test.go", ".test.ts", ".test.tsx"))
    )


sizes = {}
for group, prefix in groups.items():
    selected = [path for path in files if path.startswith(prefix) and source(path)]
    sizes[group] = {
        "files": len(selected),
        "physical_lines": sum(len(files[path].splitlines()) for path in selected),
    }

coupled = []
imports = collections.Counter()
framework_directories = collections.Counter()
tool_declarations = 0
for path, content in files.items():
    if not path.startswith(groups["framework"]) or not path.endswith(".py"):
        continue
    framework_directories[path.split("/")[3]] += len(content.splitlines())
    modules = set()
    for node in ast.walk(ast.parse(content)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(target, ast.Name) and target.id == "tool":
                    tool_declarations += 1
    imports.update(modules)
    if modules & {"langchain", "langchain_core", "langgraph", "deepagents"}:
        coupled.append(path)

lock = tomllib.loads(files["uv.lock"])
selected_dependencies = {
    "deepagents",
    "langchain",
    "langchain-core",
    "langgraph",
    "langgraph-api",
    "langgraph-sdk",
    "langgraph-checkpoint",
    "langgraph-runtime-inmem",
    "openshell",
    "fastapi",
    "pydantic",
    "neo4j",
    "mcp",
    "openai",
    "anthropic",
}
inventory = {
    "revision": revision,
    "method": "git archive; physical lines including blanks/comments; named tests excluded from component source groups; submodule contents excluded",
    "components": sizes,
    "language_totals_including_tests": {
        extension: {
            "files": sum(path.endswith(extension) for path in files),
            "physical_lines": sum(
                len(content.splitlines())
                for path, content in files.items()
                if path.endswith(extension)
            ),
        }
        for extension in (*extensions, ".sh", ".md")
    },
    "framework_directory_physical_lines": dict(framework_directories.most_common()),
    "framework_direct_framework_imports": dict(
        sorted(imports.items(), key=lambda entry: (-entry[1], entry[0]))
    ),
    "framework_coupled_files": sorted(coupled),
    "decorated_framework_tool_declarations": tool_declarations,
    "runtime_skill_documents": sum(
        path.startswith(groups["framework"] + "skills/") and path.endswith("/SKILL.md")
        for path in files
    ),
    "static_langgraph_graphs": list(json.loads(files["langgraph.json"])["graphs"]),
    "python_lock_entries_including_dev_and_workspace": len(lock["package"]),
    "locked_dependencies": {
        package["name"]: package["version"]
        for package in lock["package"]
        if package["name"] in selected_dependencies
    },
    "largest_component_sources": [
        {"path": path, "physical_lines": lines}
        for lines, path in sorted(
            (len(content.splitlines()), path)
            for path, content in files.items()
            if source(path) and any(path.startswith(prefix) for prefix in groups.values())
        )[-25:][::-1]
    ],
}
print(json.dumps(inventory, indent=2, ensure_ascii=False))
