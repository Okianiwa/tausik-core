"""SENAR Rule 5 - structured AC evidence parser (v1.4).

Replaces the v1.3 keyword-counting heuristic with a parser that extracts:
  - per-AC verification status (numbered AC items 1..N)
  - evidence type: test_ref | manual | review_ref | none
  - evidence location: e.g. "tests/test_foo.py::test_bar" or "manual run"

This module does NOT decide whether a task is closeable - it returns a
structured report that callers (QG-2 checklist) consume to produce richer
warnings than "no checklist items found in notes".

Public API:
  parse_ac_text(ac_text)         -> list[str] of AC item bodies (1-indexed)
  parse_evidence_lines(notes)    -> list[EvidenceLine]
  match_evidence_to_ac(ac_items, evidence_lines) -> AcCoverageReport
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from tausik_utils import ServiceError

CHECK_MARK_RE = re.compile(r"[\u2713\u2714\u2705]|\[v\]")
AC_NUMBER_PREFIX_RE = re.compile(r"^\s*(?:AC[-\s]*)?(\d+)[\.\)]?\s*(.*)$", re.IGNORECASE)
TEST_REF_RE = re.compile(
    r"(tests?/[\w/.\-]+\.py(?:::[\w_]+)?|test_[\w_]+\.py(?:::[\w_]+)?)",
    re.IGNORECASE,
)
NEGATIVE_RE = re.compile(
    r"\bnegative\b|\b\u043d\u0435\u0433\u0430\u0442\u0438\u0432", re.IGNORECASE
)
MANUAL_RE = re.compile(
    r"\bmanual(?:ly)?\b|\b\u0432\u0440\u0443\u0447\u043d\u0443\u044e\b|\b\u0440\u0443\u0447\u043d",
    re.IGNORECASE,
)
REVIEW_RE = re.compile(r"/review|review\s*record|adversarial", re.IGNORECASE)
# Journal lines carry a timestamp prefix, so the marker is searched, not anchored.
AC_VERIFIED_MARKER_RE = re.compile(r"\bAC\s+verified\b[:\s]*", re.IGNORECASE)
# Guarded like gate_negative_scenario's splitter: a digit or dot right before
# the number ("v1.4", "3680. 2") blocks the cut.
_INLINE_ITEM_RE = re.compile(r"\s*(?:^|[^.\d])(\d+)[.)]\s+")


def _split_inline_numbered(text: str) -> list[str]:
    """Break '1. \u2026 2. \u2026 3. \u2026' written as one line into per-item strings."""
    if not text:
        return []
    normalized = _INLINE_ITEM_RE.sub(r"\n\1. ", text)
    return [ln.strip() for ln in normalized.splitlines() if ln.strip()]


@dataclass
class EvidenceLine:
    raw: str
    ac_index: int | None
    has_checkmark: bool
    test_refs: list[str] = field(default_factory=list)
    is_manual: bool = False
    is_negative: bool = False
    is_review: bool = False

    @property
    def evidence_type(self) -> str:
        if self.test_refs:
            return "test_ref"
        if self.is_manual:
            return "manual"
        if self.is_review:
            return "review_ref"
        if self.has_checkmark:
            return "checkmark_only"
        return "none"


@dataclass
class AcCoverageItem:
    ac_index: int
    ac_text: str
    evidence: list[EvidenceLine] = field(default_factory=list)

    @property
    def has_any_evidence(self) -> bool:
        return any(e.evidence_type != "none" for e in self.evidence)

    @property
    def has_test_ref(self) -> bool:
        return any(e.test_refs for e in self.evidence)

    @property
    def has_manual(self) -> bool:
        return any(e.is_manual for e in self.evidence)


@dataclass
class AcCoverageReport:
    total_ac: int
    items: list[AcCoverageItem]
    unmatched_evidence: list[EvidenceLine]
    has_negative_evidence: bool

    @property
    def covered(self) -> int:
        return sum(1 for i in self.items if i.has_any_evidence)

    @property
    def covered_with_tests(self) -> int:
        return sum(1 for i in self.items if i.has_test_ref)

    @property
    def coverage_pct(self) -> float:
        if not self.total_ac:
            return 0.0
        return round(self.covered / self.total_ac * 100, 1)

    def gaps(self) -> list[int]:
        return [i.ac_index for i in self.items if not i.has_any_evidence]

    def to_summary(self) -> str:
        lines = [
            f"AC coverage: {self.covered}/{self.total_ac} ({self.coverage_pct}%)",
            f"  with test refs: {self.covered_with_tests}/{self.total_ac}",
        ]
        if self.gaps():
            gap_str = ", ".join(str(i) for i in self.gaps())
            lines.append(f"  gaps (no evidence): AC {gap_str}")
        if not self.has_negative_evidence:
            lines.append("  negative scenario: NOT EXERCISED in evidence")
        return "\n".join(lines)


def parse_ac_text(ac_text: str) -> list[str]:
    """Return AC item bodies in declaration order (1-indexed by position).

    Inline numbering is normalized first: a single-line AC "1. a 2. b"
    counts as two criteria, not one (QG-0 accepts that shape, so QG-2
    must count it the same way).
    """
    if not ac_text:
        return []
    items: list[str] = []
    for raw in _split_inline_numbered(ac_text):
        m = AC_NUMBER_PREFIX_RE.match(raw)
        if m and m.group(2).strip():
            items.append(m.group(2).strip())
    if not items:
        items = [ln.strip() for ln in ac_text.splitlines() if ln.strip()]
    return items


def parse_evidence_lines(notes_text: str) -> list[EvidenceLine]:
    """Parse task notes into a list of EvidenceLine candidates.

    A journal line "[ts] AC verified: 1. … ✓ 2. … ✓" carries several
    criteria in one physical line — the remainder after the marker is
    split on inline numbering and each item is parsed on its own.
    Without numbered items after the marker ("AC verified: всё хорошо")
    the line goes through the ordinary path and earns nothing.
    """
    if not notes_text:
        return []
    out: list[EvidenceLine] = []
    for raw in notes_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        segments = [line]
        marker = AC_VERIFIED_MARKER_RE.search(line)
        if marker:
            numbered = [
                seg
                for seg in _split_inline_numbered(line[marker.end() :])
                if (m := AC_NUMBER_PREFIX_RE.match(seg)) and m.group(2).strip()
            ]
            if numbered:
                segments = numbered
        for segment in segments:
            out.extend(_parse_single_line(segment))
    return out


def _parse_single_line(line: str) -> list[EvidenceLine]:
    """Ordinary per-line parse: signals + explicit AC-N / N. index refs."""
    line_has_check = bool(CHECK_MARK_RE.search(line))
    line_test_refs = TEST_REF_RE.findall(line)
    line_manual = bool(MANUAL_RE.search(line))
    line_negative = bool(NEGATIVE_RE.search(line))
    line_review = bool(REVIEW_RE.search(line))

    ac_indices: list[int] = []
    m = AC_NUMBER_PREFIX_RE.match(line)
    if m and m.group(2).strip():
        try:
            ac_indices.append(int(m.group(1)))
        except (TypeError, ValueError):
            pass
    for inline in re.finditer(r"\bAC[-\s]*(\d+)\b", line, re.IGNORECASE):
        try:
            ac_indices.append(int(inline.group(1)))
        except (TypeError, ValueError):
            continue
    ac_indices = list(dict.fromkeys(ac_indices)) or [None]  # type: ignore[list-item]

    out: list[EvidenceLine] = []
    for ac_idx in ac_indices:
        ev = EvidenceLine(
            raw=line,
            ac_index=ac_idx,
            has_checkmark=line_has_check,
            test_refs=line_test_refs,
            is_manual=line_manual,
            is_negative=line_negative,
            is_review=line_review,
        )
        if (
            ev.ac_index is not None
            or ev.has_checkmark
            or ev.test_refs
            or ev.is_manual
            or ev.is_negative
            or ev.is_review
        ):
            out.append(ev)
    return out


def match_evidence_to_ac(
    ac_items: list[str], evidence_lines: list[EvidenceLine]
) -> AcCoverageReport:
    """Map evidence lines to AC items by explicit `AC-N`/`N.` prefix."""
    items = [AcCoverageItem(ac_index=idx + 1, ac_text=text) for idx, text in enumerate(ac_items)]
    by_idx = {i.ac_index: i for i in items}
    unmatched: list[EvidenceLine] = []
    for ev in evidence_lines:
        if ev.ac_index is not None and ev.ac_index in by_idx:
            by_idx[ev.ac_index].evidence.append(ev)
        else:
            unmatched.append(ev)
    has_neg = any(ev.is_negative for ev in evidence_lines)
    return AcCoverageReport(
        total_ac=len(items),
        items=items,
        unmatched_evidence=unmatched,
        has_negative_evidence=has_neg,
    )


def build_report(ac_text: str, notes_text: str) -> AcCoverageReport:
    """Top-level helper used by QG-2 checklist."""
    ac_items = parse_ac_text(ac_text)
    evidence = parse_evidence_lines(notes_text)
    return match_evidence_to_ac(ac_items, evidence)


def evidence_json_to_prose(raw: str) -> str:
    """Convert agent-supplied JSON evidence into the canonical prose form.

    Schema:
      {"ac_evidence": [
         {"n": int>=1, "status": "pass"|"fail", "evidence": str,
          "manual": bool?, "negative": bool?},
         ...
       ]}

    Output (one line per AC item, prefixed with 'AC verified:' header so
    parse_evidence_lines + service_gates._verify_ac recognise the marker):

      AC verified:
      1. ✓ tests/foo.py::test_bar
      2. ✓ manual: smoke run on prod
      3. FAIL: regression in edge case

    Raises ServiceError on any schema violation. No DB / IO.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ServiceError("invalid --evidence-json: empty input")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ServiceError(f"invalid --evidence-json: {e.msg} (line {e.lineno})") from e
    if not isinstance(data, dict):
        raise ServiceError("invalid --evidence-json: top-level must be an object")
    items = data.get("ac_evidence")
    if not isinstance(items, list):
        raise ServiceError("invalid --evidence-json: 'ac_evidence' must be a list")
    if not items:
        raise ServiceError("invalid --evidence-json: 'ac_evidence' is empty")
    lines: list[str] = ["AC verified:"]
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ServiceError(f"invalid --evidence-json: ac_evidence[{idx}] must be an object")
        n = item.get("n")
        # bool is subclass of int — exclude explicitly.
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ServiceError(
                f"invalid --evidence-json: ac_evidence[{idx}].n must be a positive integer"
            )
        status = item.get("status")
        if status not in ("pass", "fail"):
            raise ServiceError(
                f"invalid --evidence-json: ac_evidence[{idx}].status must be 'pass' or 'fail'"
            )
        evidence = item.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ServiceError(
                f"invalid --evidence-json: ac_evidence[{idx}].evidence must be a non-empty string"
            )
        marker = "✓" if status == "pass" else "FAIL:"
        tags: list[str] = []
        if item.get("manual"):
            tags.append("manual")
        if item.get("negative"):
            tags.append("negative")
        prefix = f"{n}. {marker}"
        if tags:
            prefix += " " + " ".join(tags) + ":"
        lines.append(f"{prefix} {evidence.strip()}")
    return "\n".join(lines)
