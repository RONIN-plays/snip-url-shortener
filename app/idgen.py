"""Short-code generation.

Goal: turn a unique counter (1, 2, 3, ...) into 7-character codes that
  1. never collide                -> the mapping must be a bijection
  2. don't reveal the sequence    -> consecutive IDs must look unrelated

Approach: counter -> Feistel permutation -> base62.

Why not hash the URL?  Hashes can collide (so you need check-and-retry), and the
same URL would always get the same code, which breaks per-user links/stats.
Why not a plain counter?  Codes would be enumerable: anyone can walk
aaaaaab, aaaaaac, ... and scrape every link in the system.

A Feistel network is a classic way to build a *keyed permutation*: it is
invertible by construction, so two different inputs can never give the same
output (no collisions, no retries, no extra DB round trip).

NOTE: this is obfuscation, not encryption. It stops casual enumeration; it is
not meant to resist a determined cryptanalyst.
"""

import hashlib
import hmac

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = 62
CODE_LENGTH = 7
SPACE = BASE**CODE_LENGTH  # 3,521,614,606,208 possible codes (~3.5 trillion)

# The Feistel network works on 42 bits (2^42 = 4.4e12 >= SPACE), split in two
# 21-bit halves.
_HALF_BITS = 21
_HALF_MASK = (1 << _HALF_BITS) - 1
_ROUNDS = 4


def _round_fn(half: int, round_no: int, key: bytes) -> int:
    """Pseudo-random function of one half. It does NOT need to be invertible -
    that's the beauty of a Feistel network."""
    msg = f"{round_no}:{half}".encode()
    digest = hmac.new(key, msg, hashlib.sha256).digest()
    return int.from_bytes(digest[:4], "big") & _HALF_MASK


def _feistel(n: int, key: bytes) -> int:
    left, right = n >> _HALF_BITS, n & _HALF_MASK
    for r in range(_ROUNDS):
        left, right = right, left ^ _round_fn(right, r, key)
    return (left << _HALF_BITS) | right


def scramble(n: int, secret: str) -> int:
    """Bijective map on [0, SPACE).

    The Feistel permutes all 2^42 values, but we only want values < 62^7.
    'Cycle walking' fixes that: if the result falls outside the range, permute
    again until it lands inside. Still a bijection, and on average only
    ~1.25 iterations because SPACE is ~80% of 2^42.
    """
    if not 0 <= n < SPACE:
        raise ValueError("counter out of range")
    key = secret.encode()
    x = _feistel(n, key)
    while x >= SPACE:
        x = _feistel(x, key)
    return x


def base62_encode(n: int) -> str:
    """Encode to exactly CODE_LENGTH chars (left-padded with '0')."""
    chars = []
    for _ in range(CODE_LENGTH):
        n, rem = divmod(n, BASE)
        chars.append(ALPHABET[rem])
    return "".join(reversed(chars))


def generate_code(counter: int, secret: str) -> str:
    return base62_encode(scramble(counter, secret))
