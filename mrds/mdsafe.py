"""Neutralise candidate-controlled text before it reaches Markdown, a terminal, or a CI log.

Every value that originates in a run.json (run ids, query ids, error messages, settings values, ...) is treated as
hostile: control characters, newlines and bidi/format characters become visible escapes, long values are cut, and the
text is shown inside an inline code span whose fence is longer than any backtick run it contains.
"""

from __future__ import annotations

import re
import unicodedata

MAX_FIELD = 300
_UNSAFE_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
# Prose is only ever placed after a label on its own line, so block-level markers cannot start a line; inline
# constructs (emphasis, links, images, HTML, entities, tables, code) are what must be neutralised.
_MD_SPECIALS = re.compile(r"([\\`*_{}\[\]()#|<>~&$])")


def visible(value) -> str:
    """One line of text with no control, format or line-separator characters; long values are truncated."""
    s = "null" if value is None else str(value)
    out = []
    for ch in s:
        if ch in "\t\r\n" or unicodedata.category(ch) in _UNSAFE_CATEGORIES:
            out.append(f"\\u{ord(ch):04x}" if ord(ch) <= 0xFFFF else f"\\U{ord(ch):08x}")
        else:
            out.append(ch)
    s = "".join(out)
    return s if len(s) <= MAX_FIELD else s[:MAX_FIELD] + "...(truncated)"


def code_span(value, cell: bool = False) -> str:
    """Inline code span safe for any input. With cell=True the text is also safe inside a table cell."""
    s = visible(value)
    if cell:
        s = s.replace("|", "\\u007c")
    if not s:
        s = "(empty)"
    longest = max((len(run) for run in re.findall(r"`+", s)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if s[0] in "` " or s[-1] in "` " else ""
    return f"{fence}{pad}{s}{pad}{fence}"


def plain(value) -> str:
    """Prose-safe text: visible() plus a backslash before every Markdown punctuation character."""
    return _MD_SPECIALS.sub(r"\\\1", visible(value))
