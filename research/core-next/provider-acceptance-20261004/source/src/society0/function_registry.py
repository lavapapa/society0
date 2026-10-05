"""提供方 strict 工具参数的验证与无损 nullable 归一化。"""
import copy
from typing import Any, Dict


def validate_strict_function_parameters(schema: Dict[str, Any]) -> None:
    """Validate the JSON Schema subset required by strict function tools.

    Strict schemas must close every object, require every declared property,
    and define the item schema for arrays. Optional values should therefore be
    represented as required nullable properties.
    """

    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(
            "invalid JSON Schema for strict function parameters: "
            f"{exc.message}"
        ) from exc

    def includes_type(value: Any, expected: str) -> bool:
        if isinstance(value, str):
            return value == expected
        if isinstance(value, list):
            return expected in value
        return False

    def visit(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            raise ValueError(f"strict function schema at {path} must be an object")
        unsupported_reference_keywords = {"$ref", "$defs", "definitions"} & set(node)
        if unsupported_reference_keywords:
            keywords = ", ".join(sorted(unsupported_reference_keywords))
            raise ValueError(
                f"strict function schema at {path} uses unsupported reference keyword(s): {keywords}"
            )

        if includes_type(node.get("type"), "object"):
            properties = node.get("properties")
            if not isinstance(properties, dict):
                raise ValueError(f"strict function schema object at {path} must define properties")
            if node.get("additionalProperties") is not False:
                raise ValueError(
                    f"strict function schema object at {path} must set additionalProperties=false"
                )
            required = node.get("required")
            if not isinstance(required, list) or set(required) != set(properties):
                raise ValueError(
                    f"strict function schema object at {path} must require every declared property; "
                    "represent optional values as nullable"
                )
            for property_name, property_schema in properties.items():
                visit(property_schema, f"{path}.{property_name}")

        if includes_type(node.get("type"), "array"):
            if "items" not in node:
                raise ValueError(f"strict function schema array at {path} must define items")
            visit(node["items"], f"{path}[]")

        for keyword in ("anyOf", "oneOf", "allOf"):
            alternatives = node.get(keyword)
            if alternatives is None:
                continue
            if not isinstance(alternatives, list) or not alternatives:
                raise ValueError(f"strict function schema {keyword} at {path} must be a non-empty array")
            for index, alternative in enumerate(alternatives):
                visit(alternative, f"{path}.{keyword}[{index}]")

    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("strict function parameters must use a root object schema")
    visit(schema, "$")


def normalize_strict_function_parameters(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Return a strict-compatible copy of an ordinary function schema.

    Provider strict mode requires closed objects and every declared property in
    ``required``. Properties that were optional remain semantically optional by
    accepting ``null``; environment wrappers may then recover a declared
    non-null default before calling the action.
    """

    normalized = copy.deepcopy(schema)

    def allows_null(node: Dict[str, Any]) -> bool:
        enum = node.get("enum")
        if isinstance(enum, list) and None not in enum:
            return False
        node_type = node.get("type")
        if node_type == "null":
            return True
        if isinstance(node_type, list) and "null" in node_type:
            return True
        return any(
            isinstance(alternative, dict) and allows_null(alternative)
            for keyword in ("anyOf", "oneOf")
            for alternative in (node.get(keyword) or [])
        )

    def make_nullable(node: Any) -> Dict[str, Any]:
        if not isinstance(node, dict):
            raise ValueError("function schema properties must be JSON Schema objects")
        if allows_null(node):
            return node
        node_type = node.get("type")
        if isinstance(node_type, str):
            node["type"] = [node_type, "null"]
            if isinstance(node.get("enum"), list):
                node["enum"].append(None)
            return node
        if isinstance(node_type, list):
            if "null" not in node_type:
                node["type"] = [*node_type, "null"]
            if isinstance(node.get("enum"), list) and None not in node["enum"]:
                node["enum"].append(None)
            return node
        if isinstance(node.get("anyOf"), list):
            node["anyOf"].append({"type": "null"})
            return node
        return {"anyOf": [node, {"type": "null"}]}

    def visit(node: Any, *, is_root: bool = False) -> None:
        if not isinstance(node, dict):
            raise ValueError("function schema nodes must be JSON Schema objects")

        node_type = node.get("type")
        is_object = node_type == "object" or (
            isinstance(node_type, list) and "object" in node_type
        )
        if is_object:
            declared_properties = node.get("properties")
            additional_properties = node.get("additionalProperties")
            if (
                (not is_root and declared_properties is None)
                or additional_properties is True
                or isinstance(additional_properties, dict)
            ):
                raise ValueError(
                    "cannot normalize a free-form object for strict function tools; "
                    "declare its properties explicitly"
                )
            properties = node.setdefault("properties", {})
            if not isinstance(properties, dict):
                raise ValueError("function schema object properties must be a mapping")
            originally_required = set(node.get("required") or [])
            for property_name, property_schema in list(properties.items()):
                if property_name not in originally_required:
                    property_schema = make_nullable(property_schema)
                    properties[property_name] = property_schema
                visit(property_schema)
            node["required"] = list(properties)
            node["additionalProperties"] = False

        is_array = node_type == "array" or (
            isinstance(node_type, list) and "array" in node_type
        )
        if is_array and "items" in node:
            visit(node["items"])

        for keyword in ("anyOf", "oneOf", "allOf"):
            alternatives = node.get(keyword)
            if isinstance(alternatives, list):
                for alternative in alternatives:
                    visit(alternative)

    visit(normalized, is_root=True)
    validate_strict_function_parameters(normalized)
    return normalized
