"""Per-topic running memory, stored as `topic_memory/<source_id>.md`.

Same shape as senior-research-digest's topic memory (an "Established findings"
and an "Emerging threads" section whose bullets cite PMIDs), with two changes:

- The file is keyed by the stable `source_id`, never the display label, so
  renaming a digest in sources.json can't orphan its memory.
- Every bullet carries a slug id and a machine-readable PMID tail, so the file
  round-trips: the model returns memory as structured JSON, the code renders
  it, and the next run parses it back to check each PMID.

    # Topic Memory: Dermatology & Skin Science
    _Last updated: 2026-09-14 · source_id: dermatology-skin · covers studies through 2026-09-13_

    ## Established findings
    - **slug-id** — What recurs, in a sentence (PMIDs 1, 2, 3)

    ## Emerging threads
    - **slug-id** — What is starting to show (PMIDs 4, 5; first seen 2026-08-24)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

HEADER_RE = re.compile(
    r"^_Last updated: (?P<updated>[\d-]+) · source_id: (?P<sid>[^ ]+) · "
    r"covers studies through (?P<through>[\d-]+)_$"
)
ITEM_RE = re.compile(
    r"^- \*\*(?P<id>[^*]+)\*\* — (?P<text>.*?)"
    r"(?: \((?:PMIDs? (?P<pmids>\d[\d, ]*?))?(?:; )?"
    r"(?:first seen (?P<first>\d{4}-\d{2}-\d{2}))?\))?$"
)


def slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return s[:60].rstrip("-") or "thread"


def _clean_text(text: str) -> str:
    """One line, no trailing PMID tail the model may have added itself."""
    text = " ".join((text or "").split())
    text = re.sub(r"\s*\((?:PMIDs?|first seen)[^)]*\)\s*$", "", text)
    return text.replace("**", "")


@dataclass
class MemoryItem:
    id: str
    text: str
    pmids: list[str] = field(default_factory=list)
    first_seen: str = ""

    def render(self, *, emerging: bool) -> str:
        parts = []
        if self.pmids:
            parts.append(f"PMIDs {', '.join(self.pmids)}")
        if emerging and self.first_seen:
            parts.append(f"first seen {self.first_seen}")
        tail = f" ({'; '.join(parts)})" if parts else ""
        return f"- **{self.id}** — {_clean_text(self.text)}{tail}"


@dataclass
class TopicMemory:
    source_id: str
    label: str
    established: list[MemoryItem] = field(default_factory=list)
    emerging: list[MemoryItem] = field(default_factory=list)
    last_updated: str = ""
    covered_through: str = ""

    def items(self) -> list[MemoryItem]:
        return [*self.established, *self.emerging]

    def ids(self) -> set[str]:
        return {i.id for i in self.items()}

    def find(self, item_id: str) -> MemoryItem | None:
        return next((i for i in self.items() if i.id == item_id), None)

    def render(self) -> str:
        lines = [
            f"# Topic Memory: {self.label}",
            f"_Last updated: {self.last_updated} · source_id: {self.source_id} · "
            f"covers studies through {self.covered_through}_",
            "",
            "## Established findings",
        ]
        lines += [i.render(emerging=False) for i in self.established] or ["- _None yet._"]
        lines += ["", "## Emerging threads"]
        lines += [i.render(emerging=True) for i in self.emerging] or ["- _None yet._"]
        return "\n".join(lines) + "\n"


def parse(text: str, source_id: str, label: str) -> TopicMemory:
    mem = TopicMemory(source_id=source_id, label=label)
    section = None
    for raw in text.splitlines():
        line = raw.rstrip()
        header = HEADER_RE.match(line)
        if header:
            mem.last_updated = header["updated"]
            mem.covered_through = header["through"]
            continue
        if line.startswith("## "):
            name = line[3:].strip().lower()
            section = "established" if name.startswith("established") else (
                "emerging" if name.startswith("emerging") else None
            )
            continue
        if section is None or not line.startswith("- **"):
            continue
        m = ITEM_RE.match(line)
        if not m:
            continue
        pmids = [p for p in re.split(r"[,\s]+", m["pmids"] or "") if p]
        item = MemoryItem(id=m["id"].strip(), text=m["text"].strip(), pmids=pmids,
                          first_seen=m["first"] or "")
        getattr(mem, section).append(item)
    return mem


def path_for(memory_dir: Path, source_id: str) -> Path:
    return Path(memory_dir) / f"{source_id}.md"


def load(memory_dir: Path, source_id: str, label: str) -> TopicMemory | None:
    path = path_for(memory_dir, source_id)
    if not path.exists():
        return None
    return parse(path.read_text(encoding="utf-8"), source_id, label)


def save(memory_dir: Path, mem: TopicMemory) -> Path:
    path = path_for(memory_dir, mem.source_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(mem.render(), encoding="utf-8")
    return path


def from_model(payload: dict, *, source_id: str, label: str) -> TopicMemory:
    """Build a TopicMemory from the model's structured `memory` object."""
    def items(rows, *, emerging):
        out, seen = [], set()
        for row in rows or []:
            item_id = slugify(row.get("id") or row.get("text", ""))
            while item_id in seen:
                item_id = f"{item_id}-2"
            seen.add(item_id)
            out.append(MemoryItem(
                id=item_id,
                text=_clean_text(row.get("text", "")),
                pmids=[re.sub(r"\D", "", str(p)) for p in row.get("pmids", []) if re.sub(r"\D", "", str(p))],
                first_seen=str(row.get("first_seen") or "") if emerging else "",
            ))
        return out

    return TopicMemory(
        source_id=source_id,
        label=label,
        established=items(payload.get("established"), emerging=False),
        emerging=items(payload.get("emerging"), emerging=True),
    )
