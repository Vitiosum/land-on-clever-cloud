"""controls.py — THE sanitizer of untrusted display text: one definition for the whole skill.

A pure library: Python 3.9 standard library (re) only, so the pure modules (handoff.py) can use it as well as common.py, which re-exports it as
common.CONTROL_RX (every script, and prove.sh's log masker through `common.py mask`). deliverables.py and mdsafe.py read it from common.

    CONTROL_RX   every character of Unicode categories Cc (C0, DEL, C1 with NEL), Cf (soft hyphen, the bidirectional controls U+202A-E and U+2066-9,
                 U+200B-F, word joiners, BOM, the Arabic number signs, tags…) and Zl / Zp (U+2028, U+2029). Display: each becomes a blank
                 (CONTROL_RX.sub(" ", text)); a value that must hold none is refused when CONTROL_RX.search(value) matches.

Why: a text that reads one way and means another (bidirectional controls, invisible characters) or one line that a reader splits in two (U+2028:
str.splitlines() breaks on it) cannot pass. The selftest checks that it covers every such character of the running Python's Unicode database.
"""
import re

CONTROL_RX = re.compile(r"[\x00-\x1f\x7f-\x9f\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e\u200b-\u200f\u2028-\u202e\u2060-\u2064"
                        r"\u2066-\u206f\ufeff\ufff9-\ufffb\U000110bd\U000110cd\U00013430-\U0001343f\U0001bca0-\U0001bca3\U0001d173-\U0001d17a"
                        r"\U000e0001\U000e0020-\U000e007f]")
