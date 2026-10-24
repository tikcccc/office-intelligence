"""A skill is one self-contained capability the agent can call: a name, a description the model reads to
decide when to use it, a JSON schema for its inputs, and a Python function that does the work."""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Skill:
    name: str
    description: str
    input_schema: dict
    run: Callable[..., dict[str, Any]]
    writes: bool = False

    def validate(self, args: dict, *, internal: bool = False) -> None:
        """Enforce the schema we advertise; provider JSON mode is not input validation."""
        schema = self.input_schema
        if internal and self.name == "generate_weekly_brief":
            schema = {**schema, "properties": {"use_llm": {"type": "boolean"}}}
        _validate(args, schema)

    def tool_definition(self) -> dict:
        """OpenAI-style function tool, as the MiMo API expects."""
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.input_schema}}


def _validate(value, schema: dict, path: str = "arguments") -> None:
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if kind in types and type(value) is not types[kind]:
        raise ValueError(f"{path} must be {kind}.")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is not an allowed value.")
    if kind == "object":
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            raise ValueError("Unexpected skill arguments.")
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"Missing {key}.")
        for key, item in value.items():
            _validate(item, properties.get(key, {}), f"{path}.{key}")
    elif kind == "array":
        if len(value) > schema.get("maxItems", 40):
            raise ValueError(f"{path} has too many items.")
        for item in value:
            _validate(item, schema.get("items", {}), path)
    elif kind == "string" and len(value) > schema.get("maxLength", 200):
        raise ValueError(f"{path} is too long.")
    elif kind == "integer":
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            raise ValueError(f"{path} is outside the allowed range.")
