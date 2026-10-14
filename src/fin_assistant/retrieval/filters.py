from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SearchFilter:
    sources: tuple[str, ...] = ()
    bin: str | None = None
    doc_types: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.sources or self.bin or self.doc_types)

    def sql(self) -> tuple[str, list[Any]]:
        parts, params = [], []
        if self.sources:
            parts.append("d.source = ANY(%s)")
            params.append(list(self.sources))
        if self.bin:
            parts.append("d.bin = %s")
            params.append(self.bin)
        if self.doc_types:
            parts.append("d.doc_type = ANY(%s)")
            params.append(list(self.doc_types))
        return ("".join(f" AND {p}" for p in parts), params)
