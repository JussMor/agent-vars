from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
import json
import re


OUTPUT_TYPES = {"string", "url", "integer", "number", "boolean", "json"}


def validate_service_output(contract: dict[str, Any], service_name: str | None, key: str, value: str, *, slot: str | None = None) -> dict[str, Any]:
    if not service_name:
        raise ValueError("--service is required for typed output publication")
    service = (contract.get("services") or {}).get(service_name)
    if not isinstance(service, dict):
        raise ValueError(f"unknown publisher service: {service_name}")
    outputs = service.get("outputs")
    if outputs is None:
        raise ValueError(f"service {service_name} has no declared output schemas")
    if not isinstance(outputs, dict):
        raise ValueError(f"services.{service_name}.outputs must be a mapping")
    parts = key.split(".")
    if len(parts) != 4 or parts[0] != "service" or parts[1] != service_name or parts[2] not in {"primary", "replica"}:
        raise ValueError(f"output key must be service.{service_name}.<primary|replica>.<output>")
    if slot and parts[2] != slot:
        raise ValueError(f"output key slot {parts[2]!r} does not match --slot {slot!r}")
    output_name = parts[3]
    schema = outputs.get(output_name)
    if not isinstance(schema, dict):
        raise ValueError(f"undeclared output {output_name!r} for service {service_name}")
    kind = str(schema.get("type", "string"))
    if kind not in OUTPUT_TYPES:
        raise ValueError(f"unsupported output type {kind!r} for {service_name}.{output_name}")
    _validate_type(kind, value, service_name, output_name)
    if isinstance(schema.get("enum"), list) and value not in {str(item) for item in schema["enum"]}:
        raise ValueError(f"output {service_name}.{output_name} must be one of {schema['enum']}")
    if schema.get("pattern") and not re.fullmatch(str(schema["pattern"]), value):
        raise ValueError(f"output {service_name}.{output_name} does not match its required pattern")
    if schema.get("max_length") is not None and len(value) > int(schema["max_length"]):
        raise ValueError(f"output {service_name}.{output_name} exceeds max_length")
    return schema


def _validate_type(kind: str, value: str, service: str, output: str) -> None:
    valid = True
    if kind == "url":
        parsed = urlparse(value)
        valid = bool(parsed.scheme and (parsed.netloc or parsed.path))
    elif kind == "integer":
        try:
            int(value)
        except ValueError:
            valid = False
    elif kind == "number":
        try:
            float(value)
        except ValueError:
            valid = False
    elif kind == "boolean":
        valid = value.lower() in {"true", "false", "1", "0"}
    elif kind == "json":
        try:
            json.loads(value)
        except json.JSONDecodeError:
            valid = False
    if not valid:
        raise ValueError(f"output {service}.{output} is not valid {kind}")
