"""QG-0 negative-scenario detection (v1.3.4 med-batch-2-qg #1).

Old detector did `kw in ac_text` substring match — "Works without errors"
satisfied the gate because "error" substring was present. This module
provides a boundary-aware replacement that splits AC into per-criterion
lines (handling inline `1. ... 2. ...` numbering), redacts negation
phrases ("no", "without", "never", "нет", "без", "не должно") plus their
~60-char span, then looks for surviving NEGATIVE_SCENARIO_KEYWORDS
matches at word boundaries.

Lives separately from `service_gates.py` for filesize-gate compliance.
"""

from __future__ import annotations

import re


# Words that name a *problem*. A negation in front of one of these cancels it:
# "works without any errors" promises the absence of trouble, which is not a
# scenario anybody has to handle.
PROBLEM_KEYWORDS = (
    "error",
    "fail",
    "invalid",
    "reject",
    "401",
    "403",
    "404",
    "422",
    "500",
    "ошиб",  # корень, а не «ошибк»: иначе «без ошибок» не опознаётся как обещание
    "невалидн",
    "отказ",
    "некорректн",
    "denied",
    "unauthorized",
    "timeout",
    "negative",
    "негативн",
    "не должн",
    "не может",
    "запрещ",
    "блокир",
    "exceed",
    "overflow",
    "refuse",
    "forbid",
    "block",
    "deny",
    "break",
    "crash",
    "exception",
    # Russian parity (qg0-negative-detector-russian-parity, convention #170): the
    # working language is Russian, but several English markers above had no
    # Russian counterpart, so a criterion that named its negative case in Russian
    # ("НЕГАТИВ: ...") failed QG-0. Each of these mirrors an English keyword
    # already in the list — additive, no new failure mode.
    "негатив",  # negative
    "таймаут",  # timeout
    "исключени",  # exception (исключение/исключения)
    "паден",  # crash (падение/упал)
    "крах",  # crash
    "превыш",  # exceed (превышение/превышен)
    "переполн",  # overflow (переполнение)
    # qg0-negative-detector-does-not-know-the-word-negative (#301): the
    # task-done evidence detector `ac_evidence_detectors.NEGATIVE_RE` matches the
    # negative-WORD family `negative | негативн | отрицательн`, but QG-0 knew only
    # the first two — so a criterion naming its negative case as "отрицательный"
    # passed the evidence check yet was BLOCKED at start. The two are pinned to
    # agree by `test_negative_word_parity_with_evidence_detector`; keep this stem
    # here (and add any new NEGATIVE_RE form) so they never diverge again.
    "отрицательн",  # negative (отрицательный/отрицательное)
)


# Words that name an *absent input*: an empty journal, a missing key, a null
# value. These are the boundary cases themselves, so a negation in front of one
# ("без единой записи", "with no config file") states the scenario rather than
# denying it — and must not be redacted away with it.
ABSENCE_KEYWORDS = (
    "пуст",
    "отсутств",
    "empty",
    "missing",
    "not found",
    "none",
    "null",
)

# Kept as the union under its old name: other modules import it.
NEGATIVE_SCENARIO_KEYWORDS = PROBLEM_KEYWORDS + ABSENCE_KEYWORDS


def _boundary_re(words):
    return re.compile(r"(?<![\w])(?:" + "|".join(re.escape(k) for k in words) + r")", re.IGNORECASE)


_PROBLEM_RE = _boundary_re(PROBLEM_KEYWORDS)
_ABSENCE_RE = _boundary_re(ABSENCE_KEYWORDS)
_NEG_KW_RE = _boundary_re(NEGATIVE_SCENARIO_KEYWORDS)
# "без ключей", "without a config": what follows names the thing that is
# missing, not the trouble it causes.
_ABSENCE_PHRASE_RE = re.compile(r"\b(?:без|without|with no|no)\s+(\S+)", re.IGNORECASE)
_LOOKAHEAD = 40
# Negation prefix that *cancels* a negative keyword on the same line. The
# match consumes up to the next sentence boundary (.,;\n) or 60 non-sentence
# chars so the keyword the negation governs gets redacted along with it.
# "Works without any errors expected" → fully consumed, leaving "Works".
_NEG_NEGATION_RE = re.compile(
    r"\b(?:no|without|never|нет|без|никогда|не\s+должно?\s+быть)\b[^.;\n]{0,60}",
    re.IGNORECASE,
)


def _split_ac_into_criteria(ac_text: str) -> list[str]:
    """Split AC into per-criterion lines.

    Two separators recognized:
      - newlines (most natural)
      - inline numbering "1." "2." "3." etc. (single-line ACs the user
        wrote without breaks: "AC: 1.Works 2.Errors handled")
    """
    if not ac_text:
        return []
    normalized = re.sub(r"\s*(?:^|[^.\d])(\d+)[.)]\s+", r"\n\1. ", ac_text)
    return [ln.strip() for ln in normalized.splitlines() if ln.strip()]


def _absence_stated(line: str) -> bool:
    """Does the line describe something that will be missing?

    "без ключей", "with no config file" — the criterion is about an input that
    is not there. The negation rule used to swallow these along with the next
    sixty characters, which is exactly where the words that mark them live: a
    task whose AC said «прогон без единой записи (журнал пуст)» was refused for
    having no negative scenario at all.

    The exception is the phrase the negation rule exists for: when what follows
    names trouble rather than a missing thing ("without any errors"), the line
    is promising an absence of problems, not describing one.
    """
    for match in _ABSENCE_PHRASE_RE.finditer(line):
        tail = line[match.start(1) : match.end(1) + _LOOKAHEAD]
        if not _PROBLEM_RE.search(tail):
            return True
    return False


def has_negative_scenario(ac_text: str) -> bool:
    """True iff AC articulates at least one negative scenario.

    Per-line scan, three ways in: the line names a missing input outright
    (empty, null, отсутствует), it describes one ("без ключей"), or it names a
    problem that no negation cancels.

    Empty AC returns False (caller decides whether to error or warn).
    """
    if not ac_text:
        return False
    for line in _split_ac_into_criteria(ac_text):
        if _ABSENCE_RE.search(line) or _absence_stated(line):
            return True
        if _PROBLEM_RE.search(_NEG_NEGATION_RE.sub(" ", line)):
            return True
    return False
