"""Page text helpers (stage 2): judge what the site said, never what the user typed.

A page's `innerText` holds the user's own chat bubble, so a prompt that quotes the site's refusal sentence would be
read as the site speaking. The cure is to drop every page line that sits wholly inside the prompt before judging.
Both sides are folded first (NFKC, invisible characters, typographic quotes), because the site filters invisible
characters when it renders and a curly quote is enough to slip past a plain comparison.
"""

import unicodedata

# Invisible characters: they change the string that is compared, not what a reader sees. Every format character
# (category Cf: zero-width, bidi, invisible operators, soft hyphen, BOM, language tags...) plus the ones that are
# not Cf but draw nothing: the combining grapheme joiner, the Hangul fillers (U+3164 and U+FFA0 become U+1160 under
# NFKC), variation selectors and the tag characters.
_ALWAYS = frozenset({0x034F, 0x115F, 0x1160})
_RANGES = ((0xFE00, 0xFE0F), (0xE0100, 0xE01EF), (0xE0000, 0xE007F))


def _invisible(ch: str) -> bool:
    cp = ord(ch)
    return cp in _ALWAYS or unicodedata.category(ch) == "Cf" or any(lo <= cp <= hi for lo, hi in _RANGES)

# Typographic quotes and dashes -> straight forms. NFKC does not do this (they are not compatibility forms).
_TYPOGRAPHIC = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "′": "'", "″": '"', "´": "'", "`": "'",
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    "…": "...",
})


def norm_for_match(text: str) -> str:
    """NFKC, invisible characters removed, typographic quotes straightened, whitespace collapsed, lower-cased."""
    folded = "".join(c for c in unicodedata.normalize("NFKC", text or "") if not _invisible(c))
    return " ".join(folded.translate(_TYPOGRAPHIC).split()).lower()


def strip_echoed_prompt(page_text: str, prompt: str) -> str:
    """The page text without the lines that only echo `prompt`.

    A line the site wrote itself that also sits wholly inside the prompt cannot be told from an injected one, so it
    goes too: losing a signal only makes the caller wait longer, trusting a forged one can retire a whole pool."""
    if not page_text or not prompt:
        return page_text or ""
    norm_prompt = norm_for_match(prompt)
    if not norm_prompt:
        return page_text
    kept = []
    for line in page_text.splitlines():
        norm_line = norm_for_match(line)
        if norm_line and norm_line in norm_prompt:
            continue
        kept.append(line)
    return "\n".join(kept)
