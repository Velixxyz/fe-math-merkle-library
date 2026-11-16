"""Generate Merkle multiproof test vectors + the Fe tests that consume them.

Ground truth: the dual-implementation keccak oracle (keccak_oracle.py) and
an independent literal port of OpenZeppelin's `processMultiProof`. The
tree builder is validated against the Fe stdlib's own merkle fixture
roots (ROOT_2 / ROOT_3 / ROOT_8) before any vector is trusted.

Outputs (paths relative to the repo root):
  vectors/merkle_vectors.json  — audit file: every tree, leaf, proof,
                                 flags, and expected root.
  src/gen_merkle_tests.fe      — generated Fe #[test] functions.

Conventions (matching OpenZeppelin's StandardMerkleTree and the Fe
stdlib's `merkle` module):
  * pair hash: keccak256 of the sorted pair (commutativeKeccak256).
  * tree: pair adjacent nodes left to right; an odd last node is promoted
    unchanged (only used to validate the builder against ROOT_3; all
    multiproof vectors use perfect trees, as OZ requires).
  * distributor leaf: keccak256(keccak256(abi.encode(index, account,
    amount))) — the double hash is OZ's leaf domain separation.

Multiproof generation: queue simulation mirroring OZ's consumption
discipline (leaves in index order, then computed hashes in creation
order). Every generated vector is then validated by the independent OZ
port below, which must reproduce the tree root.
"""

import json
import random
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from keccak_oracle import hex_word, keccak256, word

ROOT = Path(__file__).parent.parent
MAX_U256 = 2**256 - 1

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def hash_pair_sorted(a: bytes, b: bytes) -> bytes:
    lo, hi = (a, b) if a <= b else (b, a)
    return keccak256(lo + hi)


def build_levels(leaves: list[bytes]) -> list[list[bytes]]:
    """Tree levels, level 0 = leaves; odd last node promoted unchanged."""
    levels = [list(leaves)]
    while len(levels[-1]) > 1:
        cur = levels[-1]
        nxt = []
        for i in range(0, len(cur) - 1, 2):
            nxt.append(hash_pair_sorted(cur[i], cur[i + 1]))
        if len(cur) % 2 == 1:
            nxt.append(cur[-1])
        levels.append(nxt)
    return levels


def single_proof(levels: list[list[bytes]], index: int) -> list[bytes]:
    """Standard single-leaf proof: the sibling at each level."""
    proof = []
    idx = index
    for level in levels[:-1]:
        sib = idx ^ 1
        if sib < len(level):
            proof.append(level[sib])
        idx //= 2
    return proof


def oz_process_multi_proof(
    proof: list[bytes], proof_flags: list[bool], leaves: list[bytes]
) -> bytes:
    """Literal port of OpenZeppelin MerkleProof.processMultiProof (v5.6).

    Independent of the generator: it consumes (proof, flags, leaves) with
    the queue discipline from the OZ source and returns the computed root.
    Raises ValueError on the same conditions OZ reverts.
    """
    leaves_len = len(leaves)
    proof_len = len(proof)
    flags_len = len(proof_flags)
    if leaves_len + proof_len != flags_len + 1:
        raise ValueError("MerkleProofInvalidMultiproof: length mismatch")
    hashes: list[bytes] = [b""] * flags_len
    leaf_pos = 0
    hash_pos = 0
    proof_pos = 0
    for i in range(flags_len):
        if leaf_pos < leaves_len:
            a = leaves[leaf_pos]
            leaf_pos += 1
        else:
            a = hashes[hash_pos]
            hash_pos += 1
        if proof_flags[i]:
            if leaf_pos < leaves_len:
                b = leaves[leaf_pos]
                leaf_pos += 1
            else:
                b = hashes[hash_pos]
                hash_pos += 1
        else:
            b = proof[proof_pos]
            proof_pos += 1
        hashes[i] = hash_pair_sorted(a, b)
    if flags_len > 0:
        if proof_pos != proof_len:
            raise ValueError("MerkleProofInvalidMultiproof: unconsumed proof")
        return hashes[flags_len - 1]
    if leaves_len > 0:
        return leaves[0]
    return proof[0]


def fe_process_multi_proof(
    proof: list[bytes], proof_flags: list[bool], leaves: list[bytes]
) -> bytes:
    """Mirror of the Fe `process_multi_proof` control flow, asserts included.

    Used to classify tampered inputs: the Fe walk reverts (ValueError
    here) on length mismatch, queue underflow, proof exhaustion, and
    unconsumed proof — earlier than OZ in the underflow case, with the
    same observable outcome. Valid vectors must make this agree with the
    OZ port exactly.
    """
    leaf_len = len(leaves)
    proof_len = len(proof)
    flag_len = len(proof_flags)
    if leaf_len + proof_len != flag_len + 1:
        raise ValueError("length mismatch")
    if flag_len == 0:
        if leaf_len > 0:
            return leaves[0]
        return proof[0]
    hashes: list[bytes | None] = [None] * flag_len
    leaf_pos = 0
    hash_pos = 0
    proof_pos = 0
    for i in range(flag_len):
        if leaf_pos < leaf_len:
            a = leaves[leaf_pos]
            leaf_pos += 1
        else:
            if not hash_pos < i:
                raise ValueError("queue underflow")
            a = hashes[hash_pos]
            hash_pos += 1
        if proof_flags[i]:
            if leaf_pos < leaf_len:
                b = leaves[leaf_pos]
                leaf_pos += 1
            else:
                if not hash_pos < i:
                    raise ValueError("queue underflow")
                b = hashes[hash_pos]
                hash_pos += 1
        else:
            if not proof_pos < proof_len:
                raise ValueError("proof exhausted")
            b = proof[proof_pos]
            proof_pos += 1
        hashes[i] = hash_pair_sorted(a, b)
    if proof_pos != proof_len:
        raise ValueError("unconsumed proof")
    result = hashes[flag_len - 1]
    assert result is not None
    return result


def gen_multiproof(
    levels: list[list[bytes]], targets: list[int]
) -> tuple[list[bytes], list[bytes], list[bool]]:
    """Generate (leaves, proof, flags) for `targets` in a perfect tree.

    Queue simulation: leaves in index order, computed parents appended to
    the back. A sibling that is the next queue item yields flag=true; any
    other sibling is emitted to the proof with flag=false. The walk runs
    until the single remaining queue node is the tree root.
    """
    targets = sorted(set(targets))
    top_level = len(levels) - 1
    leaves = [levels[0][i] for i in targets]
    if not targets:
        # Empty-set case: OZ returns proof[0] when there are no flags.
        return [], [levels[top_level][0]], []
    queue: deque[tuple[int, int]] = deque((0, i) for i in targets)
    proof: list[bytes] = []
    flags: list[bool] = []
    while not (len(queue) == 1 and queue[0] == (top_level, 0)):
        level, idx = queue.popleft()
        sib = idx ^ 1
        if queue and queue[0] == (level, sib):
            queue.popleft()
            flags.append(True)
        else:
            proof.append(levels[level][sib])
            flags.append(False)
        queue.append((level + 1, idx // 2))
    return leaves, proof, flags


# ---------------------------------------------------------------------------
# Validation of the builder against the Fe stdlib fixture roots
# ---------------------------------------------------------------------------

FIXTURE_ROOTS = {
    2: "0x891370df4fadf33f50e41f7c8a791e680c0655695ea3404385a909c8f5e13fb4",
    3: "0x038a2f0e22b3057e8442080dd34c298001ff0796b494235f125d681d5507068c",
    8: "0x8e612b28dcb28b22120543ab6da08e0d31eec6eb2722d5d43a2afd6b0ebdba48",
}


def validate_builder() -> None:
    """Reproduce the std fixture roots from keccak256(abi.encode(i)) leaves."""
    for n, expected in FIXTURE_ROOTS.items():
        leaves = [keccak256(word(i)) for i in range(n)]
        root = build_levels(leaves)[-1][0]
        assert hex_word(int.from_bytes(root, "big")) == expected, (
            f"builder mismatch for n={n}: got {root.hex()}"
        )
    print("tree builder validated against std fixture roots (2/3/8)")


# ---------------------------------------------------------------------------
# Distributor trees
# ---------------------------------------------------------------------------


def distributor_leaf(index: int, account: int, amount: int) -> bytes:
    inner = keccak256(word(index) + word(account) + word(amount))
    return keccak256(inner)


def make_tree(n: int) -> dict:
    """A distributor tree with n allocations and varied accounts/amounts."""
    allocations = []
    for i in range(n):
        account = 0x1111111111111111111111111111111111111111 + i * 0x1111111111111111111111111111111111111111
        account &= (1 << 160) - 1
        if account == 0:
            account = 0xDEAD
        amount = (i + 1) * 250_000_000_000_000_000 + (i * 7919)  # varied wei
        allocations.append({"index": i, "account": account, "amount": amount})
    leaves = [distributor_leaf(a["index"], a["account"], a["amount"]) for a in allocations]
    levels = build_levels(leaves)
    return {
        "n": n,
        "allocations": allocations,
        "leaves": leaves,
        "levels": levels,
        "root": levels[-1][0],
    }


def all_subsets(n: int) -> list[list[int]]:
    return [[i for i in range(n) if mask & (1 << i)] for mask in range(1 << n)]


def subset_plan(n: int, rng: random.Random) -> list[list[int]]:
    if n <= 8:
        return all_subsets(n)
    plan = [[i] for i in range(n)]
    plan += [[i, i + 1] for i in range(n - 1)]
    plan.append(list(range(n // 2)))
    plan.append(list(range(n // 2, n)))
    plan.append([i for i in range(n) if i % 2 == 0])
    plan.append([i for i in range(n) if i % 2 == 1])
    plan.append(list(range(n)))
    plan.append([])
    seen = {tuple(p) for p in plan}
    target = len(plan) + 24
    while len(plan) < target:
        k = rng.randint(1, n - 1)
        cand = sorted(rng.sample(range(n), k))
        if tuple(cand) not in seen:
            seen.add(tuple(cand))
            plan.append(cand)
    return plan


# ---------------------------------------------------------------------------
# Fe emission
# ---------------------------------------------------------------------------


def fe_words(values: list[bytes]) -> str:
    if not values:
        return "MemSlice<u256>::empty()"
    inner = ", ".join(hex_word(int.from_bytes(v, "big")) for v in values)
    return f"w([{inner}])"


def fe_flags(flags: list[bool]) -> str:
    if not flags:
        return "MemSlice<u256>::empty()"
    inner = ", ".join("1" if f else "0" for f in flags)
    return f"w([{inner}])"


def emit_fe(vectors: list[dict], path: Path) -> None:
    lines = [
        "// GENERATED by tools/gen_merkle_vectors.py — do not edit by hand.",
        "// Vectors are independently computed in Python (dual-keccak oracle",
        "// + literal OpenZeppelin processMultiProof port) and validated",
        "// against the Fe stdlib's merkle fixture roots. See",
        "// vectors/merkle_vectors.json for the audit trail.",
        "",
        "use core::ptr::{self, MemSlice}",
        "use ingot::multiproof::{self, leaf_from_words}",
        "use std::evm::merkle",
        "",
        "fn w<const N: usize>(_ values: [u256; N]) -> ref MemSlice<u256> {",
        "    var array = unsafe { ptr::alloc_array<u256>(N) }",
        "    var i: usize = 0",
        "    for value in values {",
        "        array[i] = value",
        "        i += 1",
        "    }",
        "    array.as_slice()",
        "}",
        "",
    ]
    for v in vectors:
        name = v["name"]
        root = hex_word(int.from_bytes(v["root"], "big"))
        if v["kind"] == "single":
            leaf = hex_word(int.from_bytes(v["leaf"], "big"))
            lines.append("#[test]")
            lines.append(f"fn {name}() {{")
            lines.append(f"    let proof = {fe_words(v['proof'])}")
            lines.append(f"    assert!(merkle::verify(proof: proof, root: {root}, leaf: {leaf}))")
            lines.append("}")
        elif v["kind"] == "multi":
            lines.append("#[test]")
            lines.append(f"fn {name}() {{")
            lines.append(f"    let proof = {fe_words(v['proof'])}")
            lines.append(f"    let flags = {fe_flags(v['flags'])}")
            lines.append(f"    let leaves = {fe_words(v['leaves'])}")
            lines.append(f"    assert!(multiproof::verify_multi_proof(proof: proof, proof_flags: flags, root: {root}, leaves: leaves))")
            lines.append(f"    assert!(multiproof::process_multi_proof(proof: proof, proof_flags: flags, leaves: leaves) == {root})")
            lines.append("}")
        elif v["kind"] == "multi_negative":
            lines.append("#[test]")
            lines.append(f"fn {name}() {{")
            lines.append(f"    let proof = {fe_words(v['proof'])}")
            lines.append(f"    let flags = {fe_flags(v['flags'])}")
            lines.append(f"    let leaves = {fe_words(v['leaves'])}")
            lines.append("    assert!(!multiproof::verify_multi_proof(proof: proof, proof_flags: flags, root: " + root + ", leaves: leaves))")
            lines.append("}")
        elif v["kind"] == "multi_revert":
            lines.append("#[test(should_revert)]")
            lines.append(f"fn {name}() {{")
            lines.append(f"    let proof = {fe_words(v['proof'])}")
            lines.append(f"    let flags = {fe_flags(v['flags'])}")
            lines.append(f"    let leaves = {fe_words(v['leaves'])}")
            lines.append("    let _ = multiproof::process_multi_proof(proof: proof, proof_flags: flags, leaves: leaves)")
            lines.append("}")
        elif v["kind"] == "leaf_encoding":
            a = v["allocation"]
            leaf = hex_word(int.from_bytes(v["leaf"], "big"))
            lines.append("#[test]")
            lines.append(f"fn {name}() {{")
            lines.append(f"    assert!(leaf_from_words([{a['index']}, {hex_word(a['account'])}, {a['amount']}]) == {leaf})")
            lines.append("}")
        lines.append("")
    path.write_text("\n".join(lines))
    print(f"wrote {path} ({len(vectors)} tests)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    validate_builder()
    rng = random.Random(1337)
    audit: dict = {"trees": [], "vectors": []}
    vectors: list[dict] = []

    for n in (2, 4, 8, 16):
        tree = make_tree(n)
        root = tree["root"]
        audit["trees"].append(
            {
                "n": n,
                "root": hex_word(int.from_bytes(root, "big")),
                "allocations": [
                    {
                        "index": a["index"],
                        "account": hex_word(a["account"]),
                        "amount": a["amount"],
                    }
                    for a in tree["allocations"]
                ],
                "leaves": [hex_word(int.from_bytes(x, "big")) for x in tree["leaves"]],
            }
        )
        # Leaf-encoding vectors: every leaf of every tree.
        for a, leaf in zip(tree["allocations"], tree["leaves"]):
            vectors.append(
                {
                    "name": f"leaf_enc_{n}_{a['index']}",
                    "kind": "leaf_encoding",
                    "allocation": a,
                    "leaf": leaf,
                    "root": root,
                }
            )
        # Single-proof vectors: every leaf of every tree.
        for i in range(n):
            proof = single_proof(tree["levels"], i)
            leaf = tree["leaves"][i]
            # Validate with the OZ single-proof discipline (sorted pairs).
            cur = leaf
            for p in proof:
                cur = hash_pair_sorted(cur, p)
            assert cur == root, f"single proof failed to reach root (n={n}, i={i})"
            vectors.append(
                {
                    "name": f"sp_{n}_leaf_{i}",
                    "kind": "single",
                    "proof": proof,
                    "leaf": leaf,
                    "root": root,
                }
            )
        # Multiproof vectors.
        for subset in subset_plan(n, rng):
            leaves, proof, flags = gen_multiproof(tree["levels"], subset)
            computed = oz_process_multi_proof(proof, flags, leaves)
            assert computed == root, f"OZ port mismatch (n={n}, subset={subset})"
            # The Fe control-flow mirror must agree exactly on valid inputs.
            assert fe_process_multi_proof(proof, flags, leaves) == root, (
                f"Fe mirror mismatch (n={n}, subset={subset})"
            )
            suffix = "_".join(map(str, subset)) if subset else "empty"
            vectors.append(
                {
                    "name": f"mp_{n}_subset_{suffix}",
                    "kind": "multi",
                    "proof": proof,
                    "flags": flags,
                    "leaves": leaves,
                    "root": root,
                    "subset": subset,
                }
            )

    # Negative vectors: tamper with valid vectors. Structure-preserving
    # tampers (proof/leaf values, leaf order, wrong root) complete the walk
    # with a wrong root -> Fe `verify` must return false. Structure-changing
    # tampers (flag flips) may instead trip a Fe assert -> classified by
    # the Fe mirror and emitted as revert tests when they do.
    base_tree = make_tree(8)
    base_root = base_tree["root"]
    base_leaves, base_proof, base_flags = gen_multiproof(
        base_tree["levels"], [0, 1, 4, 5]
    )
    assert oz_process_multi_proof(base_proof, base_flags, base_leaves) == base_root

    def tampered_word(v: bytes) -> bytes:
        return bytes([v[0] ^ 1]) + v[1:]

    tamper_cases = []
    # (a) tamper one proof element (structure-preserving)
    tamper_cases.append(
        ("tampered_proof", [tampered_word(base_proof[0])] + base_proof[1:], base_flags, base_leaves)
    )
    # (b) flip one flag (structure-changing: classified by the Fe mirror)
    f2 = list(base_flags)
    f2[0] = not f2[0]
    tamper_cases.append(("flipped_flag_0", base_proof, f2, base_leaves))
    f3 = list(base_flags)
    f3[-1] = not f3[-1]
    tamper_cases.append(("flipped_flag_last", base_proof, f3, base_leaves))
    # (c) replace one leaf (structure-preserving)
    tamper_cases.append(
        ("tampered_leaf", base_proof, base_flags, [tampered_word(base_leaves[0])] + base_leaves[1:])
    )
    # (d) swap two leaves across pair boundaries: same multiset, different
    #     consumption order. (Swapping within a sibling pair would be
    #     invisible: the pair hash is commutative.)
    l3 = list(base_leaves)
    l3[0], l3[2] = l3[2], l3[0]
    tamper_cases.append(("swapped_leaves", base_proof, base_flags, l3))
    for name, proof, flags, leaves in tamper_cases:
        try:
            computed = fe_process_multi_proof(proof, flags, leaves)
        except ValueError:
            kind = "multi_revert"
        else:
            assert computed != base_root, f"tamper case {name} still reaches root"
            kind = "multi_negative"
        vectors.append(
            {
                "name": f"mp_{'revert' if kind == 'multi_revert' else 'neg'}_{name}",
                "kind": kind,
                "proof": proof,
                "flags": flags,
                "leaves": leaves,
                "root": base_root,
            }
        )
    # Wrong root: a valid proof against a different root must be false.
    other_root = make_tree(4)["root"]
    assert other_root != base_root
    vectors.append(
        {
            "name": "mp_neg_wrong_root",
            "kind": "multi_negative",
            "proof": base_proof,
            "flags": base_flags,
            "leaves": base_leaves,
            "root": other_root,
        }
    )

    # Revert vectors: length mismatch and unconsumed proof. Each must trip
    # the Fe mirror (the behavior under test).
    revert_cases = [
        ("length_too_many_flags", base_proof, base_flags + [True], base_leaves),
        ("length_too_few_flags", base_proof, base_flags[:-1], base_leaves),
        ("length_missing_leaf", base_proof, base_flags, base_leaves[:-1]),
        ("length_extra_proof", base_proof + [base_proof[0]], base_flags, base_leaves),
        ("empty_everything", [], [], []),
    ]
    for name, proof, flags, leaves in revert_cases:
        try:
            fe_process_multi_proof(proof, flags, leaves)
        except ValueError:
            pass
        else:
            raise AssertionError(f"revert case {name} did not fail in Fe mirror")
        vectors.append(
            {
                "name": f"mp_revert_{name}",
                "kind": "multi_revert",
                "proof": proof,
                "flags": flags,
                "leaves": leaves,
                "root": base_root,
            }
        )
    # Queue underflow: flags valid in count, but flag=true consumes queue
    # items that do not exist yet. OZ would read zero-initialized memory
    # and revert only at the post-loop check; Fe reverts at the underflow
    # assert. Same observable outcome (revert).
    l0, l1, l2 = base_tree["leaves"][:3]
    underflow = {
        "name": "mp_revert_queue_underflow",
        "kind": "multi_revert",
        "proof": [base_proof[0]],
        "flags": [True, True, True],
        "leaves": [l0, l1, l2],
        "root": base_root,
    }
    try:
        fe_process_multi_proof(underflow["proof"], underflow["flags"], underflow["leaves"])
    except ValueError:
        pass
    else:
        raise AssertionError("queue underflow case did not fail in Fe mirror")
    vectors.append(underflow)

    # Audit JSON (bytes -> hex).
    def ser(v):
        if isinstance(v, bytes):
            return hex_word(int.from_bytes(v, "big"))
        if isinstance(v, dict):
            return {k: ser(x) for k, x in v.items()}
        if isinstance(v, list):
            return [ser(x) for x in v]
        return v

    audit["vectors"] = [
        {k: ser(x) for k, x in vec.items() if k != "allocation"}
        for vec in vectors
        if vec["kind"] in ("multi", "multi_negative", "multi_revert")
    ]
    (ROOT / "vectors").mkdir(exist_ok=True)
    (ROOT / "vectors" / "merkle_vectors.json").write_text(
        json.dumps(audit, indent=2) + "\n"
    )
    print(f"wrote vectors/merkle_vectors.json ({len(audit['vectors'])} multiproof vectors)")

    emit_fe(vectors, ROOT / "src" / "gen_merkle_tests.fe")

    kinds: dict[str, int] = {}
    for vec in vectors:
        kinds[vec["kind"]] = kinds.get(vec["kind"], 0) + 1
    print(f"total generated tests: {len(vectors)} {kinds}")


if __name__ == "__main__":
    main()
