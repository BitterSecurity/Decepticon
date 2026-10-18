#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic==2.12.3", "PyYAML==6.0.3", "typer==0.20.0"]
# ///
# How to run: install uv, then uv run model-catalog/generate.py [--check]
"""Render reviewed public data with local product policy; never fetch at runtime."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Literal

import typer
import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class CatalogError(ValueError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise CatalogError(detail)


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UpstreamLock(Document):
    repository: Literal["https://github.com/BitterSecurity/Decepticon.git"]
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: Literal["model-catalog/catalog.json"]


class RouteDumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


class Presentation(Document):
    id: str
    label: str
    provider: str
    reasoningEfforts: list[Literal["none", "low", "medium", "high", "xhigh", "max"]] = []


class PublicModel(Document):
    id: str
    params: dict[str, JsonValue]
    info: dict[str, JsonValue]
    ui: Presentation | None = None

    @model_validator(mode="after")
    def public_only(self) -> PublicModel:
        forbidden = ("auth/", "subscription/", "admin/", "runyour/")
        require(not self.id.startswith(forbidden), "Private route in public catalog")
        require(self.params.keys() <= {"model", "api_base", "max_tokens", "extra_headers", "additional_drop_params", "allowed_openai_params"}, "Unknown routing parameter")
        require(isinstance(self.params.get("model"), str), "Missing upstream model")
        require(not str(self.params["model"]).startswith(forbidden), "Private upstream")
        require(self.ui is None or self.ui.id == self.id, "UI identity mismatch")
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[^\s]+", self.id) is not None, "Invalid model identity")
        require(
            self.info.keys() <= {
                'cache_creation_input_token_cost',
                'cache_creation_input_token_cost_above_100k_tokens',
                'cache_creation_input_token_cost_above_272k_tokens',
                'cache_read_input_token_cost',
                'cache_read_input_token_cost_above_100k_tokens',
                'cache_read_input_token_cost_above_200k_tokens',
                'cache_read_input_token_cost_above_200k_tokens_priority',
                'cache_read_input_token_cost_above_272k_tokens',
                'cache_read_input_token_cost_batches',
                'cache_read_input_token_cost_flex',
                'cache_read_input_token_cost_priority',
                'input_cost_per_token',
                'input_cost_per_token_above_100k_tokens',
                'input_cost_per_token_above_200k_tokens',
                'input_cost_per_token_above_200k_tokens_priority',
                'input_cost_per_token_above_256k_tokens',
                'input_cost_per_token_above_272k_tokens',
                'input_cost_per_token_batches',
                'input_cost_per_token_cache_hit',
                'input_cost_per_token_flex',
                'input_cost_per_token_priority',
                'max_input_tokens',
                'max_output_tokens',
                'mode',
                'output_cost_per_token',
                'output_cost_per_token_above_100k_tokens',
                'output_cost_per_token_above_200k_tokens',
                'output_cost_per_token_above_200k_tokens_priority',
                'output_cost_per_token_above_256k_tokens',
                'output_cost_per_token_above_272k_tokens',
                'output_cost_per_token_batches',
                'output_cost_per_token_flex',
                'output_cost_per_token_priority',
                'pricing_checked_at',
                'pricing_next_cache_read_input_token_cost',
                'pricing_next_effective_at',
                'pricing_next_input_cost_per_token',
                'pricing_next_output_cost_per_token',
                'pricing_note',
                'pricing_promotion',
                'pricing_reference_model',
                'pricing_regular_cache_read_input_token_cost',
                'pricing_regular_input_cost_per_token',
                'pricing_regular_output_cost_per_token',
                'pricing_source',
                'pricing_status',
                'pricing_valid_until',
                'pricing_zero_reason',
                'supports_function_calling',
                'supports_prompt_caching',
                'supports_reasoning',
                'supports_xhigh_reasoning_effort',
            },
            "Unknown capability",
        )
        require(all(isinstance(v, (str, int, float, bool)) for v in self.info.values()), "Invalid capability value")
        base = self.params.get("api_base")
        require(base is None or (isinstance(base, str) and base.startswith(("https://", "os.environ/")) and not any(c.isspace() for c in base)), "Invalid public API base")
        headers = self.params.get("extra_headers", {})
        require(isinstance(headers, dict) and headers.keys() <= {"User-Agent"}, "Credential headers belong in overlays")
        for name in ("additional_drop_params", "allowed_openai_params"):
            values = self.params.get(name, [])
            require(isinstance(values, list) and all(isinstance(v, str) for v in values), "Invalid parameter list")
        return self


class Catalog(Document):
    schema_version: Literal[1]
    models: list[PublicModel]

    @model_validator(mode="after")
    def unique_models(self) -> Catalog:
        require(len({m.id for m in self.models}) == len(self.models), "Duplicate model")
        return self


class RouteSelection(Document):
    id: str | None = None
    patch: dict[str, JsonValue] = {}
    private: dict[str, JsonValue] | None = None


class Output(Document):
    providers: dict[str, dict[str, JsonValue]]
    exclude: list[str]
    routes: list[RouteSelection]
    settings: dict[str, JsonValue]
    format: Literal["yaml", "optional"]


class Picker(Document):
    path: str
    models: list[RouteSelection]
    exclude: list[str]


class Cli(Document):
    path: str
    groups: dict[str, list[str]]


class Overlay(Document):
    product: Literal["oss", "saas"]
    outputs: dict[str, Output]
    ui: Picker | None = None
    cli: Cli | None = None


def merge(base: dict[str, JsonValue], patch: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Apply a local JSON merge patch (null removes a field)."""
    result = copy.deepcopy(base)
    for key, value in patch.items():
        previous = result.get(key)
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(previous, dict):
            result[key] = merge(previous, value)
        else:
            result[key] = value
    return result


def route(model: PublicModel, output: Output) -> dict[str, JsonValue]:
    params = merge(model.params, output.providers[model.id.split('/')[0]])
    result: dict[str, JsonValue] = {"model_name": model.id, "litellm_params": params}
    if model.info:
        result["model_info"] = model.info
    return result


def render_routes(catalog: Catalog, output: Output) -> list[dict[str, JsonValue]]:
    models = {m.id: m for m in catalog.models}
    rows = []
    selected = set()
    for selection in output.routes:
        if selection.private is not None:
            rows.append(selection.private)
        else:
            assert selection.id is not None
            rows.append(merge(route(models[selection.id], output), selection.patch))
            selected.add(selection.id)
    for model in catalog.models:
        if model.id not in selected and model.id not in output.exclude and model.id.split('/')[0] in output.providers:
            rows.append(route(model, output))
    return rows


def render_picker(catalog: Catalog, overlay: Overlay) -> str:
    assert overlay.ui is not None
    models = {m.id: m for m in catalog.models}
    rows = []
    selected = set()
    for selection in overlay.ui.models:
        assert selection.id is not None
        ui = models[selection.id].ui
        assert ui is not None
        rows.append(merge(ui.model_dump(exclude_unset=True), selection.patch))
        selected.add(selection.id)
    providers = {models[n].ui.provider for n in selected if models[n].ui is not None}
    routed = {r['model_name'] for output in overlay.outputs.values() for r in render_routes(catalog, output)}
    for model in catalog.models:
        if model.ui is not None and model.ui.provider in providers and model.id in routed and model.id not in selected and model.id not in overlay.ui.exclude:
            rows.append({**model.ui.model_dump(exclude_unset=True), "note": model.ui.label, "creditEligible": False})
    return '// Generated; edit model-catalog/catalog.json and overlay.json.\nimport type { SelectableModel } from "./models";\nexport const PUBLIC_MODELS = '+json.dumps(rows, indent=2)+' as const satisfies readonly SelectableModel[];\n'


def generate(root: Path, check: bool = False) -> None:
    catalog = Catalog.model_validate_json((root / 'model-catalog/catalog.json').read_text())
    overlay = Overlay.model_validate_json((root / 'model-catalog/overlay.json').read_text())
    lock_path = root / 'model-catalog/upstream.lock.json'
    if overlay.product == 'saas':
        lock = UpstreamLock.model_validate_json(lock_path.read_text())
        require(lock.sha256 == hashlib.sha256((root / 'model-catalog/catalog.json').read_bytes()).hexdigest(), 'Pinned catalog hash mismatch')
    outputs: dict[str, str] = {}
    for path, output in overlay.outputs.items():
        rows = render_routes(catalog, output)
        match output.format:
            case 'optional':
                outputs[path] = json.dumps({'source': 'model-catalog/catalog.json + overlay.json (generated)', 'routes': rows}, indent=2)+'\n'
            case 'yaml':
                outputs[path] = '# Generated by model-catalog/generate.py; edit catalog/overlay sources.\n'+yaml.dump({'model_list': rows, **output.settings}, Dumper=RouteDumper, sort_keys=False, allow_unicode=True)
    if overlay.ui is not None:
        outputs[overlay.ui.path] = render_picker(catalog, overlay)
    if overlay.cli is not None:
        groups = copy.deepcopy(overlay.cli.groups)
        present = {name for names in groups.values() for name in names}
        for model in catalog.models:
            if model.id not in present and '*' not in model.id and any(model.id.split('/')[0] in output.providers for output in overlay.outputs.values()):
                groups.setdefault('Shared catalog / '+model.id.split('/')[0], []).append(model.id)
        outputs[overlay.cli.path] = '// Generated by model-catalog/generate.py.\nexport const CURRENT_PROD_MODELS: Record<string, string[]> = '+json.dumps(groups, indent=2)+';\n'
    for path, content in outputs.items():
        destination = root / path
        if check:
            require(destination.read_text() == content, f'Generated drift: {path}')
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content)
    print(f'{overlay.product}: {len(catalog.models)} public models, {len(outputs)} outputs; '+('clean' if check else 'generated'))


def main(check: bool = False) -> None:
    generate(Path(__file__).resolve().parents[1], check)


if __name__ == '__main__':
    typer.run(main)
