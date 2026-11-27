# SPDX-License-Identifier: MIT
"""Fractional index keys — generates the Excalidraw 0.18 element `index` (z-order).

Ported from: rocicorp/fractional-indexing 3.2.0 `src/index.js`
  https://github.com/rocicorp/fractional-indexing (web/node_modules/fractional-indexing/src/index.js)
  License: CC0-1.0 (no rights reserved). The original code is based on
  https://observablehq.com/@dgreensp/implementing-fractional-indexing.
Must produce the same keys as the JS (tested: tests/test_agentkit.py compares against node output).
Porting difference: JS `Math.round(x)` rounds .5 up, so it is ported as `floor(x + 0.5)` (Python round() rounds half to even).
Standard library only; importable on Python 3.9.
"""
from __future__ import annotations

import math
from typing import List, Optional

BASE_62_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _midpoint(a: str, b: Optional[str], digits: str) -> str:
    zero = digits[0]
    if b is not None and a >= b:
        raise ValueError(a + " >= " + b)
    if a[-1:] == zero or (b and b[-1:] == zero):
        raise ValueError("trailing zero")
    if b:
        n = 0
        while (a[n] if n < len(a) else zero) == (b[n] if n < len(b) else None):
            n += 1
        if n > 0:
            return b[:n] + _midpoint(a[n:], b[n:], digits)
    digit_a = digits.index(a[0]) if a else 0
    digit_b = digits.index(b[0]) if b is not None else len(digits)
    if digit_b - digit_a > 1:
        return digits[int(math.floor(0.5 * (digit_a + digit_b) + 0.5))]
    if b and len(b) > 1:
        return b[:1]
    return digits[digit_a] + _midpoint(a[1:], None, digits)


def _integer_length(head: str) -> int:
    if "a" <= head <= "z":
        return ord(head) - ord("a") + 2
    if "A" <= head <= "Z":
        return ord("Z") - ord(head) + 2
    raise ValueError("invalid order key head: " + head)


def _validate_integer(i: str) -> None:
    if len(i) != _integer_length(i[0]):
        raise ValueError("invalid integer part of order key: " + i)


def _integer_part(key: str) -> str:
    n = _integer_length(key[0])
    if n > len(key):
        raise ValueError("invalid order key: " + key)
    return key[:n]


def validate_order_key(key: str, digits: str = BASE_62_DIGITS) -> None:
    if not isinstance(key, str) or not key:
        raise ValueError("invalid order key: %r" % (key,))
    if key == "A" + digits[0] * 26:
        raise ValueError("invalid order key: " + key)
    i = _integer_part(key)
    f = key[len(i):]
    if f[-1:] == digits[0]:
        raise ValueError("invalid order key: " + key)
    if any(c not in digits for c in key[1:]):   # Not in the JS original — added to reject malformed keys sent by others
        raise ValueError("invalid order key: " + key)


def is_valid_key(key, digits: str = BASE_62_DIGITS) -> bool:
    try:
        validate_order_key(key, digits)
        return True
    except (ValueError, TypeError, IndexError):
        return False


def _increment_integer(x: str, digits: str) -> Optional[str]:
    _validate_integer(x)
    head, digs = x[0], list(x[1:])
    carry = True
    i = len(digs) - 1
    while carry and i >= 0:
        d = digits.index(digs[i]) + 1
        if d == len(digits):
            digs[i] = digits[0]
        else:
            digs[i] = digits[d]
            carry = False
        i -= 1
    if carry:
        if head == "Z":
            return "a" + digits[0]
        if head == "z":
            return None
        h = chr(ord(head) + 1)
        if h > "a":
            digs.append(digits[0])
        else:
            digs.pop()
        return h + "".join(digs)
    return head + "".join(digs)


def _decrement_integer(x: str, digits: str) -> Optional[str]:
    _validate_integer(x)
    head, digs = x[0], list(x[1:])
    borrow = True
    i = len(digs) - 1
    while borrow and i >= 0:
        d = digits.index(digs[i]) - 1
        if d == -1:
            digs[i] = digits[-1]
        else:
            digs[i] = digits[d]
            borrow = False
        i -= 1
    if borrow:
        if head == "a":
            return "Z" + digits[-1]
        if head == "A":
            return None
        h = chr(ord(head) - 1)
        if h < "Z":
            digs.append(digits[-1])
        else:
            digs.pop()
        return h + "".join(digs)
    return head + "".join(digs)


def generate_key_between(a: Optional[str], b: Optional[str], digits: str = BASE_62_DIGITS) -> str:
    """Key k with a < k < b. a=None means the very start, b=None the very end."""
    if a is not None:
        validate_order_key(a, digits)
    if b is not None:
        validate_order_key(b, digits)
    if a is not None and b is not None and a >= b:
        raise ValueError(a + " >= " + b)
    if a is None:
        if b is None:
            return "a" + digits[0]
        ib = _integer_part(b)
        fb = b[len(ib):]
        if ib == "A" + digits[0] * 26:
            return ib + _midpoint("", fb, digits)
        if ib < b:
            return ib
        res = _decrement_integer(ib, digits)
        if res is None:
            raise ValueError("cannot decrement any more")
        return res
    if b is None:
        ia = _integer_part(a)
        fa = a[len(ia):]
        i = _increment_integer(ia, digits)
        return ia + _midpoint(fa, None, digits) if i is None else i
    ia = _integer_part(a)
    fa = a[len(ia):]
    ib = _integer_part(b)
    fb = b[len(ib):]
    if ia == ib:
        return ia + _midpoint(fa, fb, digits)
    i = _increment_integer(ia, digits)
    if i is None:
        raise ValueError("cannot increment any more")
    if i < b:
        return i
    return ia + _midpoint(fa, None, digits)


def generate_n_keys_between(a: Optional[str], b: Optional[str], n: int, digits: str = BASE_62_DIGITS) -> List[str]:
    """n keys between a and b (ascending)."""
    if n == 0:
        return []
    if n == 1:
        return [generate_key_between(a, b, digits)]
    if b is None:
        c = generate_key_between(a, b, digits)
        out = [c]
        for _ in range(n - 1):
            c = generate_key_between(c, b, digits)
            out.append(c)
        return out
    if a is None:
        c = generate_key_between(a, b, digits)
        out = [c]
        for _ in range(n - 1):
            c = generate_key_between(a, c, digits)
            out.append(c)
        out.reverse()
        return out
    mid = n // 2
    c = generate_key_between(a, b, digits)
    return generate_n_keys_between(a, c, mid, digits) + [c] + generate_n_keys_between(c, b, n - mid - 1, digits)
