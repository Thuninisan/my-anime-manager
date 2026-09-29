"""Store JSON-compatible extension values as typed relational tree nodes."""

from sqlalchemy import delete, select

from .models import StructuredNode


def replace(session, owner_kind: str, owner_id: str | int, field: str, value) -> None:
    session.execute(delete(StructuredNode).where(
        StructuredNode.owner_kind == owner_kind,
        StructuredNode.owner_id == str(owner_id),
        StructuredNode.root_field == field))
    if value == {}:
        return

    def add(item, parent_id=None, key_name=None, position=None):
        if isinstance(item, dict):
            kind, scalar = "dict", None
        elif isinstance(item, list):
            kind, scalar = "list", None
        elif item is None:
            kind, scalar = "null", None
        elif isinstance(item, bool):
            kind, scalar = "bool", "1" if item else "0"
        elif isinstance(item, int):
            kind, scalar = "int", str(item)
        elif isinstance(item, float):
            kind, scalar = "float", repr(item)
        elif isinstance(item, str):
            kind, scalar = "str", item
        else:
            raise TypeError(f"Unsupported structured value: {type(item).__name__}")
        node = StructuredNode(owner_kind=owner_kind, owner_id=str(owner_id),
                              root_field=field, parent_id=parent_id,
                              key_name=key_name, position=position,
                              value_type=kind, value_text=scalar)
        session.add(node)
        session.flush()
        if kind == "dict":
            for key, child in item.items():
                add(child, node.id, key_name=str(key))
        elif kind == "list":
            for index, child in enumerate(item):
                add(child, node.id, position=index)

    add(value)


def read(session, owner_kind: str, owner_id: str | int, field: str, default=None):
    nodes = session.scalars(select(StructuredNode).where(
        StructuredNode.owner_kind == owner_kind,
        StructuredNode.owner_id == str(owner_id),
        StructuredNode.root_field == field).order_by(StructuredNode.id)).all()
    if not nodes:
        return default
    children = {}
    for node in nodes[1:]:
        children.setdefault(node.parent_id, []).append(node)

    def build(node):
        kind = node.value_type
        if kind == "dict":
            return {child.key_name: build(child) for child in children.get(node.id, [])}
        if kind == "list":
            return [build(child) for child in sorted(children.get(node.id, []),
                                                     key=lambda child: child.position)]
        if kind == "null":
            return None
        if kind == "bool":
            return node.value_text == "1"
        if kind == "int":
            return int(node.value_text)
        if kind == "float":
            return float(node.value_text)
        return node.value_text

    return build(nodes[0])


def clear(session, owner_kind: str, owner_id: str | int) -> None:
    session.execute(delete(StructuredNode).where(
        StructuredNode.owner_kind == owner_kind,
        StructuredNode.owner_id == str(owner_id)))
