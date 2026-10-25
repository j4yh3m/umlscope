"""Minimal EDN reader: enough for crapper / mutator snapshots.

Supports maps, vectors, lists, sets, keywords, strings, numbers, nil,
true/false, symbols, comments (;), discard (#_) and commas as whitespace.
Keywords become plain strings without the leading colon, so
{:name "x"} reads as {"name": "x"}.
"""

from __future__ import annotations

import re

_WS = set(" \t\r\n,")
_DELIM = _WS | set("()[]{}\";")
_NUM = re.compile(r"^[+-]?(\d+\.?\d*([eE][+-]?\d+)?|\.\d+)M?N?$")
_ESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


class EdnError(ValueError):
    pass


def loads(text: str):
    reader = _Reader(text)
    value = reader.read()
    reader.skip_ws()
    if reader.pos != len(text):
        raise EdnError(f"trailing data at offset {reader.pos}")
    return value


class _Reader:
    _END = object()

    def __init__(self, text: str):
        self.s = text
        self.pos = 0

    def skip_ws(self):
        s, n = self.s, len(self.s)
        while self.pos < n:
            c = s[self.pos]
            if c in _WS:
                self.pos += 1
            elif c == ";":
                while self.pos < n and s[self.pos] != "\n":
                    self.pos += 1
            else:
                break

    def read(self):
        value = self._read_form()
        if value is self._END:
            raise EdnError("unexpected closing delimiter")
        return value

    def _read_form(self):
        self.skip_ws()
        if self.pos >= len(self.s):
            raise EdnError("unexpected end of input")
        c = self.s[self.pos]
        if c in ")]}":
            self.pos += 1
            return self._END
        if c == "{":
            self.pos += 1
            items = self._read_seq("}")
            if len(items) % 2:
                raise EdnError("map with odd number of forms")
            return {_hashable(items[i]): items[i + 1] for i in range(0, len(items), 2)}
        if c in "[(":
            self.pos += 1
            return self._read_seq("]" if c == "[" else ")")
        if c == '"':
            return self._read_string()
        if c == "#":
            nxt = self.s[self.pos + 1 : self.pos + 2]
            if nxt == "{":
                self.pos += 2
                return set(map(_hashable, self._read_seq("}")))
            if nxt == "_":
                self.pos += 2
                self.read()
                return self._read_form()
            # tagged literal (#inst, #uuid, ...): read the tag, keep the value
            self.pos += 1
            self._read_token()
            return self.read()
        if c == "\\":
            self.pos += 1
            tok = self._read_token() or self.s[self.pos - 1]
            return {"newline": "\n", "space": " ", "tab": "\t"}.get(tok, tok[:1])
        return self._atom(self._read_token())

    def _read_seq(self, close: str):
        items = []
        while True:
            form = self._read_form()
            if form is self._END:
                if self.s[self.pos - 1] != close:
                    raise EdnError(f"mismatched delimiter, expected {close}")
                return items
            items.append(form)

    def _read_string(self):
        self.pos += 1
        out = []
        s, n = self.s, len(self.s)
        while self.pos < n:
            c = s[self.pos]
            if c == '"':
                self.pos += 1
                return "".join(out)
            if c == "\\":
                self.pos += 1
                e = s[self.pos : self.pos + 1]
                if e == "u":
                    out.append(chr(int(s[self.pos + 1 : self.pos + 5], 16)))
                    self.pos += 5
                    continue
                out.append(_ESC.get(e, e))
            else:
                out.append(c)
            self.pos += 1
        raise EdnError("unterminated string")

    def _read_token(self) -> str:
        start = self.pos
        while self.pos < len(self.s) and self.s[self.pos] not in _DELIM:
            self.pos += 1
        return self.s[start : self.pos]

    @staticmethod
    def _atom(tok: str):
        if tok == "nil":
            return None
        if tok == "true":
            return True
        if tok == "false":
            return False
        if tok.startswith(":"):
            return tok[1:]
        if _NUM.match(tok):
            tok = tok.rstrip("MN")
            if any(ch in tok for ch in ".eE"):
                return float(tok)
            return int(tok)
        return tok  # symbol


def _hashable(value):
    if isinstance(value, list):
        return tuple(_hashable(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _hashable(v)) for k, v in value.items()))
    if isinstance(value, set):
        return frozenset(value)
    return value
