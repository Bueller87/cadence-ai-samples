"""Call the selected catalog model from a Worker activity and print its spend.

Mock mode never calls a provider. Live mode reads repository-root models.yaml.
The default is local Ollama: reported tokens, no external API charge, and local
compute left unpriced. A missing usage field stays unavailable.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit


ALLOWED_SERVICES = (
    "api-gateway",
    "checkout",
    "inventory",
    "payments",
    "ledger",
)
DEFAULT_MODEL_ID = "llama3.2-local"
_LOCK = threading.Lock()
Transport = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]


class LiveConfigurationError(ValueError):
    """Live mode is not safely configured."""


class LiveAuthenticationError(RuntimeError):
    """The provider rejected the configured credential."""


class ModelDecisionError(RuntimeError):
    """The model response cannot be used as the next investigation action."""


@dataclass(frozen=True)
class ModelConfig:
    id: str
    provider: str
    model: str
    endpoint: str


@dataclass(frozen=True)
class Usage:
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    external_usd: float | None
    note: str


def enabled() -> bool:
    return os.environ.get("INCIDENT_TRIAGE_MODEL") == "live"


def prepare() -> ModelConfig:
    """Validate the live selection before the Worker starts polling."""
    selection = load_selection()
    _api_key(selection)
    return selection


def plan(context: dict[str, Any], transport: Transport | None = None) -> dict[str, Any]:
    selection = load_selection()
    system = (
        "You investigate one synthetic checkout failure. Reply with one JSON "
        "object only. Keys are tool, args, hypothesis, and conclusion. tool is "
        "get_logs_by_trace_id or draft. get_logs_by_trace_id args are "
        '{"service": one of api-gateway, checkout, inventory, payments, ledger}. '
        "draft args are {} and conclusion has call_path, root_cause, confidence, "
        "and summary. Use only the evidence in the user message. If hops is "
        "empty, request logs for the start service before drafting. If "
        "force_draft is true, tool must be draft."
    )
    content, usage = _complete(
        selection,
        system,
        {
            "start": context.get("start"),
            "guidance": context.get("guidance") or [],
            "force_draft": bool(context.get("force_draft")),
            "hops": _compact_hops(context.get("hops") or []),
        },
        transport,
    )
    note_spend("plan_next", usage)
    return _validate_plan(
        _parse_object(content), bool(context.get("force_draft"))
    )


def revise(
    conclusion: dict[str, Any],
    request: str,
    hops: list[dict[str, Any]],
    transport: Transport | None = None,
) -> dict[str, Any]:
    selection = load_selection()
    system = (
        "Revise this synthetic incident draft so it satisfies the review "
        "request. Reply with one JSON object only. Keys are call_path, "
        "root_cause, confidence, summary, and errors_per_hop. Ground every "
        "claim in the hop lines. call_path may contain only api-gateway, "
        "checkout, inventory, payments, and ledger."
    )
    content, usage = _complete(
        selection,
        system,
        {
            "request": request,
            "conclusion": conclusion,
            "hops": _compact_hops(hops),
        },
        transport,
    )
    note_spend("revise_draft", usage)
    return _validate_revision(_parse_object(content), request)


def spend_text() -> str:
    entries = _read_spend()
    if not entries:
        return "SPEND scripted planner, no provider call, external API $0"
    latest = entries[-1]
    return _format_line(
        str(latest["call"]),
        str(latest["model"]),
        latest.get("input_tokens"),
        latest.get("output_tokens"),
        latest.get("external_usd"),
        _totals(entries),
        str(latest["note"]),
    )


def load_selection(path: Path | None = None, model_id: str | None = None) -> ModelConfig:
    catalog = path or _catalog_path()
    selected_id = model_id or os.environ.get(
        "INCIDENT_TRIAGE_MODEL_ID", DEFAULT_MODEL_ID
    )
    selected = next(
        (entry for entry in _read_catalog(catalog) if entry.id == selected_id),
        None,
    )
    if selected is None:
        raise LiveConfigurationError(
            f"models.yaml has no model id {selected_id!r}"
        )
    _validate_endpoint(selected)
    return selected


def note_spend(call: str, usage: Usage) -> str:
    entry = {
        "call": call,
        "provider": usage.provider,
        "model": usage.model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "external_usd": usage.external_usd,
        "note": usage.note,
    }
    with _LOCK:
        path = _state_dir() / "model-spend.jsonl"
        entries = _read_spend(path)
        entries.append(entry)
        path.write_text(
            "".join(json.dumps(item, sort_keys=True) + "\n" for item in entries),
            encoding="utf-8",
        )
    line = _format_line(
        call,
        usage.model,
        usage.input_tokens,
        usage.output_tokens,
        usage.external_usd,
        _totals(entries),
        usage.note,
    )
    print(line, flush=True)
    return line


def _complete(
    selection: ModelConfig,
    system: str,
    payload: dict[str, Any],
    transport: Transport | None,
) -> tuple[str, Usage]:
    key = _api_key(selection)
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    body = {
        "model": selection.model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, sort_keys=True)},
        ],
    }
    caller = transport or _post_json
    try:
        response = caller(_chat_url(selection), body, headers)
    except (LiveAuthenticationError, ModelDecisionError):
        raise
    except Exception as error:
        raise RuntimeError("temporary model request failure") from error
    usage = _usage(selection, response)
    try:
        content = _message_content(response)
    except ModelDecisionError:
        note_spend("model", usage)
        raise
    return content, usage


def _post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        status = int(error.code)
        if status in {401, 403}:
            raise LiveAuthenticationError("model rejected MODEL_AI_KEY") from None
        if status in {408, 409, 425, 429} or status >= 500:
            raise RuntimeError("temporary model request failure") from None
        raise ModelDecisionError(f"model rejected request: HTTP {status}") from None
    except urllib.error.URLError as error:
        raise RuntimeError("temporary model request failure") from error
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ModelDecisionError("model response was not JSON") from error
    if not isinstance(parsed, dict):
        raise ModelDecisionError("model response was not a JSON object")
    return parsed


def _message_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ModelDecisionError("model response did not include a choice")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ModelDecisionError("model response did not include text")
    return content


def _parse_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ModelDecisionError("model decision was not the expected JSON") from error
    if not isinstance(parsed, dict):
        raise ModelDecisionError("model decision was not a JSON object")
    return parsed


def _validate_plan(decision: dict[str, Any], force_draft: bool) -> dict[str, Any]:
    tool = decision.get("tool")
    hypothesis = decision.get("hypothesis")
    if tool not in {"get_logs_by_trace_id", "draft"}:
        raise ModelDecisionError("model tool must be a known investigation tool")
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise ModelDecisionError("model hypothesis must be text")
    if force_draft and tool != "draft":
        raise ModelDecisionError("the repeated-call guard requires a draft")
    if tool == "get_logs_by_trace_id":
        args = decision.get("args")
        service = args.get("service") if isinstance(args, dict) else None
        if service not in ALLOWED_SERVICES:
            raise ModelDecisionError("model selected a service outside the fixture")
        return {
            "tool": tool,
            "args": {"service": service},
            "hypothesis": hypothesis,
        }
    conclusion = decision.get("conclusion")
    if not isinstance(conclusion, dict):
        raise ModelDecisionError("a draft needs a conclusion")
    return {
        "tool": "draft",
        "args": {},
        "hypothesis": hypothesis,
        "conclusion": _conclusion_fields(conclusion),
    }


def _validate_revision(decision: dict[str, Any], request: str) -> dict[str, Any]:
    revised = _conclusion_fields(decision)
    errors = decision.get("errors_per_hop", [])
    if errors is None:
        errors = []
    if not isinstance(errors, list) or not all(
        isinstance(item, str) and item.strip() for item in errors
    ):
        raise ModelDecisionError("errors_per_hop must be a list of text")
    revised["revision_request"] = request
    if errors:
        revised["errors_per_hop"] = errors
    return revised


def _conclusion_fields(conclusion: dict[str, Any]) -> dict[str, Any]:
    path = conclusion.get("call_path")
    if (
        not isinstance(path, list)
        or not path
        or any(service not in ALLOWED_SERVICES for service in path)
    ):
        raise ModelDecisionError("call_path must list known services")
    fields: dict[str, Any] = {"call_path": list(path)}
    for name in ("root_cause", "confidence", "summary"):
        value = conclusion.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ModelDecisionError(f"conclusion {name} must be text")
        fields[name] = value
    return fields


def _compact_hops(hops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for hop in hops:
        lines = hop.get("lines") if isinstance(hop, dict) else None
        compact.append(
            {
                "service": hop.get("service") if isinstance(hop, dict) else None,
                "lines": lines if isinstance(lines, list) else [],
            }
        )
    return compact


def _usage(selection: ModelConfig, response: dict[str, Any]) -> Usage:
    raw = response.get("usage")
    reported = raw if isinstance(raw, dict) else {}
    input_tokens = _token(reported.get("prompt_tokens"))
    output_tokens = _token(reported.get("completion_tokens"))
    if selection.provider == "ollama":
        return Usage(
            selection.provider,
            selection.model,
            input_tokens,
            output_tokens,
            0.0,
            "local Ollama, external API $0, local compute unpriced",
        )
    input_rate = os.environ.get("INCIDENT_TRIAGE_INPUT_USD_PER_MILLION")
    output_rate = os.environ.get("INCIDENT_TRIAGE_OUTPUT_USD_PER_MILLION")
    external: float | None = None
    note = "provider spend unavailable until usage and a price are both present"
    if (
        input_tokens is not None
        and output_tokens is not None
        and input_rate
        and output_rate
    ):
        try:
            external = (
                input_tokens * float(input_rate) + output_tokens * float(output_rate)
            ) / 1_000_000
        except ValueError:
            external = None
        else:
            note = (
                "estimate from INCIDENT_TRIAGE_INPUT_USD_PER_MILLION and "
                "INCIDENT_TRIAGE_OUTPUT_USD_PER_MILLION"
            )
    return Usage(
        selection.provider,
        selection.model,
        input_tokens,
        output_tokens,
        external,
        note,
    )


def _token(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _totals(entries: list[dict[str, Any]]) -> dict[str, int | float | None]:
    def total(name: str) -> int | float | None:
        values = [entry.get(name) for entry in entries]
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in values):
            return None
        return sum(values)

    return {
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "external_usd": total("external_usd"),
    }


def _format_line(
    call: str,
    model: str,
    input_tokens: Any,
    output_tokens: Any,
    external_usd: Any,
    totals: dict[str, int | float | None],
    note: str,
) -> str:
    return (
        f"SPEND call={call} model={model} "
        f"input_tokens={_fmt_count(input_tokens)} "
        f"output_tokens={_fmt_count(output_tokens)} "
        f"this_external_usd={_fmt_usd(external_usd)} "
        f"running_input_tokens={_fmt_count(totals['input_tokens'])} "
        f"running_output_tokens={_fmt_count(totals['output_tokens'])} "
        f"running_external_usd={_fmt_usd(totals['external_usd'])} "
        f"{note}"
    )


def _fmt_count(value: Any) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else "unavailable"


def _fmt_usd(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unavailable"
    if float(value) == 0:
        return "0"
    return f"{float(value):.8f}".rstrip("0").rstrip(".")


def _read_spend(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or (_state_dir() / "model-spend.jsonl")
    if not target.exists():
        return []
    return [
        json.loads(line)
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _state_dir() -> Path:
    path = Path(
        os.environ.get(
            "INCIDENT_TRIAGE_STATE",
            Path(__file__).resolve().parent / ".state",
        )
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _catalog_path() -> Path:
    override = os.environ.get("INCIDENT_TRIAGE_CATALOG")
    if override:
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "models.yaml"
        if candidate.is_file():
            return candidate
    raise LiveConfigurationError("models.yaml was not found")


def _read_catalog(path: Path) -> list[ModelConfig]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as error:
        raise LiveConfigurationError(f"catalog file not found: {path}") from error
    if not lines or lines[0].strip() != "models:":
        raise LiveConfigurationError("models.yaml: expected a models list")
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines[1:]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith("  - "):
            if current:
                entries.append(current)
            key, value = _split_field(line[4:])
            current = {key: value}
            continue
        if current is not None and line.startswith("    "):
            key, value = _split_field(line.strip())
            current[key] = value
            continue
        raise LiveConfigurationError("models.yaml: unsupported catalog shape")
    if current:
        entries.append(current)
    required = ("id", "provider", "model", "endpoint")
    parsed: list[ModelConfig] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        missing = next((field for field in required if not entry.get(field)), None)
        if missing:
            raise LiveConfigurationError(
                f"models.yaml: models[{index}] is missing {missing}"
            )
        if entry["id"] in seen:
            raise LiveConfigurationError(
                f"models.yaml: duplicate model id {entry['id']!r}"
            )
        seen.add(entry["id"])
        parsed.append(ModelConfig(**{field: entry[field] for field in required}))
    return parsed


def _split_field(text: str) -> tuple[str, str]:
    if ":" not in text:
        raise LiveConfigurationError("models.yaml: expected key: value")
    key, value = text.split(":", 1)
    key, value = key.strip(), value.strip()
    if not key or not value:
        raise LiveConfigurationError("models.yaml: expected key: value")
    return key, value


def _validate_endpoint(selection: ModelConfig) -> None:
    if selection.provider not in {"google", "ollama", "openai"}:
        raise LiveConfigurationError("model provider must be google, ollama, or openai")
    endpoint = urlsplit(selection.endpoint)
    if (
        endpoint.scheme not in {"http", "https"}
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise LiveConfigurationError(
            "model endpoint must be an HTTP URL without credentials, query, or fragment"
        )
    if selection.provider in {"google", "openai"} and endpoint.scheme != "https":
        raise LiveConfigurationError(
            "remote credential-bearing providers require an HTTPS endpoint"
        )
    if selection.provider == "ollama" and endpoint.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise LiveConfigurationError("Ollama requires a loopback endpoint")


def _chat_url(selection: ModelConfig) -> str:
    base = selection.endpoint.rstrip("/")
    if selection.provider == "ollama":
        return base + "/v1/chat/completions"
    if selection.provider == "openai":
        return base + "/chat/completions"
    return base + "/v1beta/openai/chat/completions"


def _api_key(selection: ModelConfig) -> str:
    if selection.provider == "ollama":
        return ""
    key = os.environ.get("MODEL_AI_KEY", "").strip()
    if not key:
        raise LiveConfigurationError("live worker requires MODEL_AI_KEY")
    return key
