"""Generate fixed-point test vectors + the Fe tests that consume them.

Ground truth: Python exact integer arithmetic (arbitrary precision) from
the same specifications the Fe code implements. Where this library
composes std primitives (notably `UFixed` power and square root), the
generator replicates std's DOCUMENTED algorithms step for step — binary
squaring with half-up steps for `**`, floor sqrt of the scaled radicand
for `sqrt` — so the vectors test this library's composition, not a
re-implementation of std internals. This is stated honestly in the
README; the rounding-to-nearest, aggregation, and growth functions
themselves are specified independently (see the module docs).

Outputs (paths relative to the repo root):
  vectors/fixed_vectors.json — audit file: every case + expected value.
  src/gen_fixed_tests.fe      — generated Fe #[test] functions.

Rounding rule under test: round-half-up. `mul_div_nearest(a, b, d)` is
floor(a*b/d), plus one exactly when the remainder is at least half the
divisor (ties round up). The Fe implementation tests `r >= d - r` to
avoid doubling the remainder.
"""

import json
import math
import random
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).parent.parent
MAX = 2**256 - 1

# ---------------------------------------------------------------------------
# Exact replications of the documented std algorithms
# ---------------------------------------------------------------------------


def mul_div_half_up(a: int, b: int, d: int) -> int:
    """std's per-step rounding for `UFixed ** n`: half up, 512-bit product."""
    q, r = divmod(a * b, d)
    if r >= d - (d >> 1):
        q += 1
    assert q <= MAX, "mul_div_half_up overflow"
    return q


def ufixed_pow(raw: int, n: int, scale: int) -> int:
    """std `Pow<u256> for UFixed<D>`: squaring, half-up steps, 0**0 = 1."""
    x, y = raw, n
    if x == 0:
        return scale if y == 0 else 0
    z = scale if (y & 1) == 0 else x
    y >>= 1
    while y != 0:
        x = mul_div_half_up(x, x, scale)
        if y & 1:
            z = mul_div_half_up(z, x, scale)
        y >>= 1
    return z


def ufixed_sqrt(raw: int, scale: int) -> int:
    """std `UFixed::sqrt`: floor sqrt of the scaled radicand."""
    return math.isqrt(raw * scale)


def ufixed_mul(a: int, b: int, scale: int) -> int:
    q = (a * b) // scale
    assert q <= MAX, "ufixed mul overflow"
    return q


def ufixed_div(a: int, b: int, scale: int) -> int:
    assert b != 0, "ufixed div by zero"
    q = (a * scale) // b
    assert q <= MAX, "ufixed div overflow"
    return q


# ---------------------------------------------------------------------------
# Specifications of THIS library's functions (independent of the Fe code)
# ---------------------------------------------------------------------------


def spec_mul_div_nearest(a: int, b: int, d: int) -> int:
    assert d != 0
    q, r = divmod(a * b, d)
    if r >= d - r:
        q += 1
    assert q <= MAX, "mul_div_nearest overflow"
    return q


def spec_checked_mul_div_nearest(a: int, b: int, d: int) -> int | None:
    if d == 0:
        return None
    q, r = divmod(a * b, d)
    if q > MAX:
        return None
    if r >= d - r:
        if q == MAX:
            return None
        q += 1
    return q


def spec_avg(a: int, b: int) -> int:
    return (a & b) + ((a ^ b) >> 1)


def spec_geometric_mean(a: int, b: int, scale: int) -> int:
    return ufixed_sqrt(ufixed_mul(a, b, scale), scale)


def spec_pow_signed(raw: int, exp: int, scale: int) -> int:
    if exp >= 0:
        return ufixed_pow(raw, exp, scale)
    p = ufixed_pow(raw, -exp, scale)
    assert p != 0, "pow_signed underflow: reciprocal of zero"
    return ufixed_div(scale, p, scale)


def spec_compound(principal: int, rate: int, periods: int, scale: int) -> int:
    growth = ufixed_pow(scale + rate, periods, scale)
    return ufixed_mul(principal, growth, scale)


def spec_discount(principal: int, rate: int, periods: int, scale: int) -> int:
    growth = ufixed_pow(scale + rate, periods, scale)
    return ufixed_div(principal, growth, scale)


def spec_sqrt_abs(raw_i256: int, scale: int) -> int:
    return ufixed_sqrt(abs(raw_i256), scale)


def spec_vested_fraction(start: int, end: int, now: int, scale: int) -> int:
    if end <= start or now >= end:
        return scale
    if now <= start:
        return 0
    return ((now - start) * scale) // (end - start)


# ---------------------------------------------------------------------------
# Case collection
# ---------------------------------------------------------------------------


def fe_int(n: int) -> str:
    """Fe literal: decimal for small values, hex for full-width words."""
    if n < 0:
        return "-" + fe_int(-n)
    if n < 2**63:
        return str(n)
    return "0x" + format(n, "x")


def find_rounding_overflow_case(rng: random.Random) -> tuple[int, int, int]:
    """Find (a, b, d) with quotient == u256::MAX and remainder >= half.

    Then the rounded result overflows: `mul_div_nearest` reverts and the
    checked variant returns None. Construction: pick a, d, then take b as
    the ceiling that lands a*b in [d*MAX + ceil(d/2), d*MAX + d).
    """
    for _ in range(100_000):
        a = rng.randint(2**255, MAX)
        d = rng.randint(2**255, a)
        target = d * MAX + (d + 1) // 2
        b = -(-target // a)
        if b > MAX:
            continue
        q, r = divmod(a * b, d)
        if q == MAX and r >= d - r:
            return a, b, d
    raise AssertionError("rounding-overflow search failed")


def collect_cases() -> list[dict]:
    rng = random.Random(20261116)
    cases: list[dict] = []

    # --- mul_div_nearest: hand-picked edges --------------------------------
    hand = [
        (1, 1, 2), (3, 1, 2), (5, 1, 2), (1, 1, 3), (2, 1, 3),
        (7, 1, 4), (5, 1, 4), (9, 1, 4), (1, 1, 1), (0, 12345, 7),
        (12345, 0, 7), (MAX, 1, 1), (MAX, 1, 2), (MAX, 2, 3),
        (2**200, 2**100, 2**50), (2**200, 2**100, 2**45),
        (10**18, 10**18, 10**18), (10**18, 3, 2), (7, 7, 2),
        (100, 100, 3), (1, 3, 2), (3, 3, 2), (2**255, 2, 2),
        (2**255 - 1, 2, 2), (999999999999999999, 999999999999999999, 10**18),
    ]
    for i, (a, b, d) in enumerate(hand):
        cases.append({
            "name": f"mdn_hand_{i}", "kind": "mul_div_nearest",
            "a": a, "b": b, "d": d, "expected": spec_mul_div_nearest(a, b, d),
        })
    # --- mul_div_nearest: seeded random ------------------------------------
    for i in range(40):
        bits_a = rng.choice([8, 32, 64, 128, 200, 255])
        bits_b = rng.choice([8, 32, 64, 128, 200, 255])
        bits_d = rng.choice([1, 8, 32, 64, 128, 200])
        a = rng.randint(0, 2**bits_a - 1)
        b = rng.randint(0, 2**bits_b - 1)
        d = rng.randint(1, 2**bits_d - 1)
        try:
            expected = spec_mul_div_nearest(a, b, d)
        except AssertionError:
            continue
        cases.append({
            "name": f"mdn_rand_{i}", "kind": "mul_div_nearest",
            "a": a, "b": b, "d": d, "expected": expected,
        })
    # --- mul_div_nearest: reverts -------------------------------------------
    a_ro, b_ro, d_ro = find_rounding_overflow_case(rng)
    for i, (a, b, d) in enumerate([(MAX, MAX, 1), (MAX, 2, 1), (1, 1, 0), (a_ro, b_ro, d_ro), (2**200, 2**100, 3)]):
        cases.append({
            "name": f"mdn_revert_{i}", "kind": "mul_div_nearest_revert",
            "a": a, "b": b, "d": d,
        })
    # --- checked_mul_div_nearest --------------------------------------------
    for i, (a, b, d) in enumerate(hand[:10]):
        cases.append({
            "name": f"cmdn_some_{i}", "kind": "checked_some",
            "a": a, "b": b, "d": d, "expected": spec_checked_mul_div_nearest(a, b, d),
        })
    for i, (a, b, d) in enumerate([(1, 1, 0), (0, 0, 0), (MAX, MAX, 1), (MAX, 2, 1), (a_ro, b_ro, d_ro)]):
        assert spec_checked_mul_div_nearest(a, b, d) is None
        cases.append({
            "name": f"cmdn_none_{i}", "kind": "checked_none",
            "a": a, "b": b, "d": d,
        })
    cases.append({
        "name": "cmdn_max_exact", "kind": "checked_some",
        "a": MAX, "b": 1, "d": 1, "expected": MAX,
    })

    # --- UFixedExt over Wad (D=18), Ray (D=27), UFixed<6> ------------------
    scales = [("Wad", 18), ("Ray", 27), ("UFixed<6>", 6)]
    for type_name, decimals in scales:
        scale = 10**decimals
        tag = {"Wad": "wad", "Ray": "ray", "UFixed<6>": "fix6"}[type_name]
        # mul_nearest: ties, exact, random
        mul_cases = [
            (1, scale // 2), (1, scale * 2 // 5), (scale, scale),
            (scale // 3, 3 * scale), (scale // 2, scale // 2),
            (123456789, 987654321), (scale, 1), (1, 1),
        ]
        for i in range(6):
            mul_cases.append((rng.randint(0, 10 * scale), rng.randint(0, 10 * scale)))
        for i, (a, b) in enumerate(mul_cases):
            cases.append({
                "name": f"{tag}_mul_nearest_{i}", "kind": "mul_nearest",
                "type": type_name, "scale": scale, "a": a, "b": b,
                "expected": spec_mul_div_nearest(a, b, scale),
            })
        # div_nearest
        div_cases = [
            (scale, 2 * scale), (scale, 3 * scale), (3 * scale, 2 * scale),
            (1, 2), (1, 3), (scale, scale), (5 * scale, 2 * scale),
            (123456789 * scale // 1000, 7 * scale // 3),
        ]
        for i in range(6):
            div_cases.append((rng.randint(0, 10 * scale), rng.randint(1, 10 * scale)))
        for i, (a, b) in enumerate(div_cases):
            cases.append({
                "name": f"{tag}_div_nearest_{i}", "kind": "div_nearest",
                "type": type_name, "scale": scale, "a": a, "b": b,
                "expected": spec_mul_div_nearest(a, scale, b),
            })
        # clamp / avg / geometric_mean
        clamp_cases = [
            (5 * scale, 2 * scale, 8 * scale),
            (1 * scale, 2 * scale, 8 * scale),
            (9 * scale, 2 * scale, 8 * scale),
            (2 * scale, 2 * scale, 8 * scale),
            (8 * scale, 2 * scale, 8 * scale),
        ]
        for i, (x, lo, hi) in enumerate(clamp_cases):
            cases.append({
                "name": f"{tag}_clamp_{i}", "kind": "clamp",
                "type": type_name, "scale": scale, "x": x, "lo": lo, "hi": hi,
                "expected": min(max(x, lo), hi),
            })
        avg_cases = [
            (0, 0), (0, MAX), (MAX, MAX), (MAX - 1, MAX),
            (2 * scale, 5 * scale), (scale, scale + 1), (1, 2),
        ]
        for i in range(4):
            avg_cases.append((rng.randint(0, MAX), rng.randint(0, MAX)))
        for i, (a, b) in enumerate(avg_cases):
            cases.append({
                "name": f"{tag}_avg_{i}", "kind": "avg",
                "type": type_name, "scale": scale, "a": a, "b": b,
                "expected": spec_avg(a, b),
            })
        gm_cases = [
            (4 * scale, 9 * scale), (2 * scale, 8 * scale),
            (scale, 2 * scale), (scale, scale), (0, 9 * scale),
            (3 * scale, 3 * scale),
        ]
        for i in range(4):
            gm_cases.append((rng.randint(0, 100 * scale), rng.randint(0, 100 * scale)))
        for i, (a, b) in enumerate(gm_cases):
            cases.append({
                "name": f"{tag}_geomean_{i}", "kind": "geometric_mean",
                "type": type_name, "scale": scale, "a": a, "b": b,
                "expected": spec_geometric_mean(a, b, scale),
            })
        # pow_signed
        pow_cases = [
            (2 * scale, 0), (2 * scale, 1), (2 * scale, 2), (2 * scale, 10),
            (2 * scale, -1), (2 * scale, -2), (4 * scale, -3),
            (scale // 2, -2), (scale // 2, 2), (3 * scale // 2, -2),
            (3 * scale // 2, 3), (scale, 100), (scale, -100),
            (0, 0), (0, 5), (0, -3), (scale + scale // 100, 100),
            (2 * scale, 64), (10 * scale, 10),
        ]
        for i, (base, exp) in enumerate(pow_cases):
            if base == 0 and exp < 0:
                cases.append({
                    "name": f"{tag}_pow_signed_revert_{i}", "kind": "pow_signed_revert",
                    "type": type_name, "scale": scale, "base": base, "exp": exp,
                })
                continue
            cases.append({
                "name": f"{tag}_pow_signed_{i}", "kind": "pow_signed",
                "type": type_name, "scale": scale, "base": base, "exp": exp,
                "expected": spec_pow_signed(base, exp, scale),
            })
        # pow_signed underflow-to-zero reciprocal -> revert (0.5^-100)
        cases.append({
            "name": f"{tag}_pow_signed_underflow_revert", "kind": "pow_signed_revert",
            "type": type_name, "scale": scale, "base": scale // 2, "exp": -100,
        })
        assert ufixed_pow(scale // 2, 100, scale) == 0
        # compound / discount
        cd_cases = [
            (1000 * scale, scale // 20, 10),
            (1000 * scale, 0, 10),
            (1000 * scale, scale // 20, 0),
            (1000 * scale, scale, 3),
            (7 * scale, scale // 100, 100),
            (scale, scale // 10, 50),
        ]
        for i, (principal, rate, periods) in enumerate(cd_cases):
            cases.append({
                "name": f"{tag}_compound_{i}", "kind": "compound",
                "type": type_name, "scale": scale,
                "principal": principal, "rate": rate, "periods": periods,
                "expected": spec_compound(principal, rate, periods, scale),
            })
            cases.append({
                "name": f"{tag}_discount_{i}", "kind": "discount",
                "type": type_name, "scale": scale,
                "principal": principal, "rate": rate, "periods": periods,
                "expected": spec_discount(principal, rate, periods, scale),
            })
        # clamp min > max -> revert; div by zero -> revert
        cases.append({
            "name": f"{tag}_clamp_revert", "kind": "clamp_revert",
            "type": type_name, "scale": scale,
            "x": 5 * scale, "lo": 8 * scale, "hi": 2 * scale,
        })
        cases.append({
            "name": f"{tag}_div_zero_revert", "kind": "div_nearest_revert",
            "type": type_name, "scale": scale, "a": scale, "b": 0,
        })

    # --- sqrt_abs (SWad) ------------------------------------------------------
    for i, raw in enumerate([-4 * 10**18, -9 * 10**18, 0, 16 * 10**18,
                             -2 * 10**18, -10**18, 25 * 10**18, -123456789 * 10**12]):
        cases.append({
            "name": f"sqrt_abs_{i}", "kind": "sqrt_abs",
            "raw": raw, "expected": spec_sqrt_abs(raw, 10**18),
        })

    # --- vested_fraction -------------------------------------------------------
    vest_cases = [
        (100, 200, 100), (100, 200, 200), (100, 200, 150),
        (100, 200, 50), (100, 200, 250), (100, 200, 133),
        (100, 100, 100), (200, 100, 150), (0, 0, 0),
        (1_000_000, 2_000_000, 1_500_000), (0, 10**9, 333_333_333),
    ]
    for i, (start, end, now) in enumerate(vest_cases):
        cases.append({
            "name": f"vested_{i}", "kind": "vested_fraction",
            "start": start, "end": end, "now": now,
            "expected": spec_vested_fraction(start, end, now, 10**18),
        })

    return cases


# ---------------------------------------------------------------------------
# Fe emission
# ---------------------------------------------------------------------------


def emit_fe(cases: list[dict], path: Path) -> None:
    lines = [
        "// GENERATED by tools/gen_fixed_vectors.py — do not edit by hand.",
        "// Expected values are computed in Python with exact integer",
        "// arithmetic. Where the library composes std primitives, the",
        "// generator replicates std's documented algorithms (see the",
        "// generator header and the README). Audit file:",
        "// vectors/fixed_vectors.json.",
        "",
        "use core::fixed::{Ray, SWad, UFixed, Wad}",
        "use ingot::fixedx::{",
        "    UFixedExt, checked_mul_div_nearest, mul_div_nearest, sqrt_abs, vested_fraction,",
        "}",
        "",
    ]
    for c in cases:
        name = c["name"]
        kind = c["kind"]
        if kind == "mul_div_nearest":
            lines += ["#[test]", f"fn {name}() {{",
                      f"    assert!(mul_div_nearest({fe_int(c['a'])}, {fe_int(c['b'])}, {fe_int(c['d'])}) == {fe_int(c['expected'])})",
                      "}", ""]
        elif kind == "mul_div_nearest_revert":
            lines += ["#[test(should_revert)]", f"fn {name}() {{",
                      f"    let _ = mul_div_nearest({fe_int(c['a'])}, {fe_int(c['b'])}, {fe_int(c['d'])})",
                      "}", ""]
        elif kind == "checked_some":
            lines += ["#[test]", f"fn {name}() {{",
                      f"    assert!(checked_mul_div_nearest({fe_int(c['a'])}, {fe_int(c['b'])}, {fe_int(c['d'])}) == Some({fe_int(c['expected'])}))",
                      "}", ""]
        elif kind == "checked_none":
            lines += ["#[test]", f"fn {name}() {{",
                      f"    assert!(checked_mul_div_nearest({fe_int(c['a'])}, {fe_int(c['b'])}, {fe_int(c['d'])}) == None)",
                      "}", ""]
        elif kind in ("mul_nearest", "div_nearest", "avg", "geometric_mean"):
            t = c["type"]
            method = {"mul_nearest": "mul_nearest", "div_nearest": "div_nearest",
                      "avg": "avg", "geometric_mean": "geometric_mean"}[kind]
            lines += ["#[test]", f"fn {name}() {{",
                      f"    assert!({t}::from_raw({fe_int(c['a'])}).{method}({t}::from_raw({fe_int(c['b'])})) == {t}::from_raw({fe_int(c['expected'])}))",
                      "}", ""]
        elif kind == "clamp":
            t = c["type"]
            lines += ["#[test]", f"fn {name}() {{",
                      f"    assert!({t}::from_raw({fe_int(c['x'])}).clamp({t}::from_raw({fe_int(c['lo'])}), {t}::from_raw({fe_int(c['hi'])})) == {t}::from_raw({fe_int(c['expected'])}))",
                      "}", ""]
        elif kind == "clamp_revert":
            t = c["type"]
            lines += ["#[test(should_revert)]", f"fn {name}() {{",
                      f"    let _ = {t}::from_raw({fe_int(c['x'])}).clamp({t}::from_raw({fe_int(c['lo'])}), {t}::from_raw({fe_int(c['hi'])}))",
                      "}", ""]
        elif kind == "div_nearest_revert":
            t = c["type"]
            lines += ["#[test(should_revert)]", f"fn {name}() {{",
                      f"    let _ = {t}::from_raw({fe_int(c['a'])}).div_nearest({t}::from_raw({fe_int(c['b'])}))",
                      "}", ""]
        elif kind == "pow_signed":
            t = c["type"]
            lines += ["#[test]", f"fn {name}() {{",
                      f"    assert!({t}::from_raw({fe_int(c['base'])}).pow_signed({fe_int(c['exp'])}) == {t}::from_raw({fe_int(c['expected'])}))",
                      "}", ""]
        elif kind == "pow_signed_revert":
            t = c["type"]
            lines += ["#[test(should_revert)]", f"fn {name}() {{",
                      f"    let _ = {t}::from_raw({fe_int(c['base'])}).pow_signed({fe_int(c['exp'])})",
                      "}", ""]
        elif kind in ("compound", "discount"):
            t = c["type"]
            lines += ["#[test]", f"fn {name}() {{",
                      f"    assert!({t}::from_raw({fe_int(c['principal'])}).{kind}({t}::from_raw({fe_int(c['rate'])}), {fe_int(c['periods'])}) == {t}::from_raw({fe_int(c['expected'])}))",
                      "}", ""]
        elif kind == "sqrt_abs":
            lines += ["#[test]", f"fn {name}() {{",
                      f"    assert!(sqrt_abs(SWad::from_raw({fe_int(c['raw'])})) == Wad::from_raw({fe_int(c['expected'])}))",
                      "}", ""]
        elif kind == "vested_fraction":
            lines += ["#[test]", f"fn {name}() {{",
                      f"    assert!(vested_fraction({c['start']}, {c['end']}, {c['now']}) == Wad::from_raw({fe_int(c['expected'])}))",
                      "}", ""]
        else:
            raise AssertionError(f"unknown kind {kind}")
    path.write_text("\n".join(lines))
    print(f"wrote {path} ({len(cases)} tests)")


def main() -> None:
    cases = collect_cases()
    (ROOT / "vectors").mkdir(exist_ok=True)
    (ROOT / "vectors" / "fixed_vectors.json").write_text(
        json.dumps(cases, indent=2) + "\n"
    )
    print(f"wrote vectors/fixed_vectors.json ({len(cases)} cases)")
    emit_fe(cases, ROOT / "src" / "gen_fixed_tests.fe")
    kinds: dict[str, int] = {}
    for c in cases:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"total generated tests: {len(cases)} {kinds}")


if __name__ == "__main__":
    main()
