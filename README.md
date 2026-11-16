# Fe Math & Merkle Library

**Pitch.** The Fe standard library ships fixed-point arithmetic and
single-proof Merkle verification — but no multiproofs, no standard leaf
encodings, and no rounding-to-nearest or growth math. This library fills
exactly those gaps: OpenZeppelin-compatible multiproof verification and
standard-tree leaf encodings, plus rounding-to-nearest, overflow-safe
aggregation, and compound/discount math over std's fixed-point types —
with 790 tests driven by independently computed vectors, and gas
benchmarks against an OpenZeppelin baseline. No duplicates, no
re-implementations of what std already does well: extensions that compose
with it.

Built with **Fe 26.6.0** (`fe 26.6.0 (5b3f96a)`) for the [Fe Builders
Challenge](https://fe-lang.org/contest/) (Nov 3–24, 2026). Solo entry by
lokie ([Velixxyz](https://github.com/Velixxyz)). MIT licensed.

## The problem

Two gaps, one pattern: the pieces Fe developers need *around* the stdlib
primitives are missing.

1. **Merkle trees.** `std::evm::merkle` verifies one leaf against one
   root. Real distributions verify *many* leaves at once: airdrop claims,
   batched allowlist checks, rollup state proofs. OpenZeppelin's
   `processMultiProof` is the standard algorithm, and Fe has no
   equivalent — nor the double-hashed leaf encoding
   (`keccak256(keccak256(abi.encode(...)))`) that OpenZeppelin's
   StandardMerkleTree uses, so Fe contracts cannot consume trees built
   by the standard off-chain tooling without hand-rolled glue.
2. **Fixed-point math.** `core::fixed` provides `UFixed<D>` / `Fixed<D>`
   with floor-rounded `mul`/`div`, `sqrt`, and `**`. What it does not
   provide: rounding to nearest (systematic downward drift in repeated
   floor-rounded computation), overflow-safe averaging, geometric means,
   signed exponents, or compound/discount factors — the operations
   vesting, fee, and yield math is built from.

This library adds both, as thin, well-specified extensions over std.

## Approach

**Compose, don't duplicate.** Every function here is specified in terms
of std primitives and inherits their audited behavior:

- Multiproofs hash pairs with std's `merkle::hash_pair_sorted` (the
  sorted-pair/commutative hash) and reuse std's `ProofNodes` trait, so
  proofs and leaves can arrive as `MemSlice`, `DynArray<u256>`, or
  `DynArray<Bytes32>` with zero-copy calldata views.
- Fixed-point extensions are methods on std's own `UFixed<D>` (via a
  trait implemented generically over the scale `D`), and the power /
  square-root steps reuse std's documented algorithms. The rounding,
  aggregation, and growth semantics are this library's own, specified
  exactly (see below) and tested against independent Python arithmetic.

**Compatibility is the feature.** The multiproof algorithm is a faithful
port of OpenZeppelin's `MerkleProof.processMultiProof` (v5.6 semantics):
same length equation, same queue discipline, same sorted-pair hashing,
same edge cases (including the empty-flags single-node case). Any tree
and multiproof valid for OpenZeppelin is valid here, and vice versa —
verified by 337 multiproof vectors validated against a literal Python
port of the OZ code. The one deliberate divergence is documented: on a
malformed proof that exhausts the hash queue, this implementation
reverts at the underflow assert instead of reading uninitialized memory
and reverting at the post-loop check — same observable outcome (revert),
earlier and with a clear message.

**Semantics first, in writing.** Each function documents its rounding,
overflow, and edge-case behavior in its doc comment, and the test
vectors pin it:

- `mul_div_nearest`: round-half-up. Ties round up. Reverts on `d == 0`
  or an overflowing (rounded) result; `checked_mul_div_nearest` returns
  `None` instead.
- `mul_nearest` / `div_nearest`: the same rule at fixed-point scale.
- `avg`: exact `floor((a + b) / 2)` for any `u256` raws, without
  overflow (bit trick, no 512-bit intermediate needed).
- `geometric_mean`: `sqrt(a * b)` with std's floor `*` then floor
  `sqrt` — double round-down, error under 2 raw units, documented.
- `pow_signed`: `self ** n` for negative `n` via the reciprocal of the
  positive power; reverts if the positive power underflows to zero.
- `compound` / `discount`: `(1 + rate) ** periods` growth and discount
  factors over std's half-up squaring.
- `vested_fraction`: linear vesting in `[0, 1]`; degenerate windows
  (`end <= start`) vest fully; clamps outside the window.

## Fe-specific highlight

This library is written *in* Fe's idioms, not transliterated from
another language:

- **Const generics.** `UFixedExt` is implemented once as
  `impl<const D: u256> UFixedExt for UFixed<D>`, so `Wad` (18 decimals),
  `Ray` (27), and any other scale get the same methods with zero
  duplication — exercised by the test suite at D = 6, 18, and 27.
- **Traits as the container abstraction.** `ProofFlags` (implemented for
  `MemSlice<u256>`, `DynArray<bool>`, `DynArray<u256>`) mirrors std's
  `ProofNodes`, so `process_multi_proof` accepts any mix of proof, flag,
  and leaf containers — including `DynArray<bool>` flags straight out of
  a contract message — with no copying or conversion.
- **Effects.** The demo contract declares exactly the effects each
  handler needs (`ctx`, `mut call`, `mut log`, the storage it reads and
  writes); the compiler checks them. Pure library functions declare no
  effects and are usable anywhere, including `const` contexts where
  applicable (`leaf_from_words` is a `const fn` over std's const
  `keccak_words` intrinsic).
- **Checked arithmetic by default.** Overflow and division-by-zero
  revert unless a function explicitly opts into a checked variant that
  returns `Option`. The rounding-overflow case (quotient exactly
  `u256::MAX` with a remainder that rounds up) is pinned by a dedicated
  vector found by search.
- **The test story is Fe-native.** 790 tests run under `fe test`,
  including EVM tests that deploy the demo contract, fund it, claim
  against independently computed trees, and assert reverts with
  `#[test(should_revert)]` — plus a Foundry harness that measures the
  compiled artifacts' gas.

## Architecture

```
fe.toml                        Ingot manifest (name fe_math_merkle)
src/
  lib.fe                       MerkleDistributor demo contract (uses both modules)
  multiproof.fe                Multiproof verification + leaf encodings
  fixedx.fe                    Fixed-point extensions over core::fixed
  bench_harness.fe             Gas-measurement harness (NOT library API)
  tests.fe                     Hand-written tests: smoke, EVM distributor, containers
  gen_merkle_tests.fe          GENERATED: 409 Merkle tests from Python vectors
  gen_fixed_tests.fe           GENERATED: 360 fixed-point tests from Python vectors
tools/
  keccak_oracle.py             Dual-implementation keccak (pycryptodome + eth_hash)
  gen_merkle_vectors.py        Merkle vector generator + OZ-port validator
  gen_fixed_vectors.py         Fixed-point vector generator (exact arithmetic)
  gen_bench.py                 Forge benchmark test generator (embeds artifacts)
vectors/
  merkle_vectors.json          Audit file: trees, proofs, flags, roots
  fixed_vectors.json           Audit file: every case + expected value
bench/
  foundry.toml                 Forge project (solc 0.8.24, optimizer, Cancun)
  src/OzVerifier.sol           OpenZeppelin baseline wrappers (deps fetched, not vendored)
  test/Bench.t.sol             GENERATED: gas benchmarks (embeds Fe bytecode)
BENCHMARKS.md                  Method, full results, honest caveats
```

### API summary

**`multiproof`** (see `src/multiproof.fe` for full docs):

```fe
pub trait ProofFlags { fn flag_len(self) -> usize; fn flag_at(self, _ index: usize) -> bool }
pub fn process_multi_proof<P: ProofNodes, L: ProofNodes, F: ProofFlags>(
    proof: P, proof_flags: F, leaves: L,
) -> u256
pub fn verify_multi_proof<P: ProofNodes, L: ProofNodes, F: ProofFlags>(
    proof: P, proof_flags: F, root: u256, leaves: L,
) -> bool
pub const fn leaf_from_words<const N: usize>(_ words: [u256; N]) -> u256
pub fn double_keccak(_ data: MemSpan) -> u256
pub fn leaf_from_packed<T: EncodePacked>(_ value: own T) -> u256
```

**`fixedx`** (see `src/fixedx.fe` for full docs):

```fe
pub fn mul_div_nearest(_ a: u256, _ b: u256, _ d: u256) -> u256
pub fn checked_mul_div_nearest(_ a: u256, _ b: u256, _ d: u256) -> Option<u256>
pub trait UFixedExt {
    fn mul_nearest(own self, _ other: own Self) -> Self
    fn div_nearest(own self, _ other: own Self) -> Self
    fn clamp(own self, _ min: own Self, _ max: own Self) -> Self
    fn avg(own self, _ other: own Self) -> Self
    fn geometric_mean(own self, _ other: own Self) -> Self
    fn pow_signed(own self, _ exp: i256) -> Self
    fn compound(own self, _ rate: own Self, _ periods: u256) -> Self
    fn discount(own self, _ rate: own Self, _ periods: u256) -> Self
}
pub fn sqrt_abs(_ x: SWad) -> Wad
pub fn vested_fraction(_ start: u256, _ end: u256, _ now: u256) -> Wad
```

**Demo contract** (`MerkleDistributor`): `fund()` (payable),
`claim(uint256[],uint256,address,uint256)`,
`claimMulti(uint256[],bool[],uint256[],address[],uint256[])`, `root()`,
`isClaimed(uint256)`, `vestedFraction()`, `totalClaimed()`, plus the
`Claimed` event. Constructor: `(root, vesting_start, vesting_end,
fee_raw)` where `fee_raw` is a `Wad` raw value (≤ 10¹⁸ = 100%).

## Build, test, benchmark

Prerequisites: [feup](https://fe-lang.org/guides/installation/) for Fe,
Python 3.10+ for the vector generators, [Foundry](https://getfoundry.sh)
for the gas benchmarks.

```sh
# Install the exact toolchain used (Fe 26.6.0)
curl -fsSL https://raw.githubusercontent.com/argotorg/fe/master/feup/feup.sh | bash -s -- --version v26.6.0
export PATH="$HOME/.fe/bin:$PATH"
fe --version   # fe 26.6.0 (5b3f96a)

# Format check, tests, build
fe fmt --check
fe test        # 790 tests
fe build       # out/MerkleDistributor.*, out/BenchHarness.*

# Regenerate the test vectors and confirm they match the committed files
# (fe fmt canonicalizes the generated sources before the comparison)
pip install pycryptodome 'eth-hash[pycryptodome]'
python3 tools/gen_merkle_vectors.py
python3 tools/gen_fixed_vectors.py
fe fmt
git diff --exit-code src/gen_merkle_tests.fe src/gen_fixed_tests.fe vectors/

# Gas benchmarks (embeds the freshly built artifacts)
python3 tools/gen_bench.py
cd bench
forge install foundry-rs/forge-std OpenZeppelin/openzeppelin-contracts
forge test -vvv
```

CI (`.github/workflows/ci.yml`) runs the format check, the full test
suite, the build, the vector-regeneration check, and the Forge
benchmarks on every push.

## Demo: the MerkleDistributor

The demo contract composes both modules in a realistic flow: a
Merkle-gated token distribution with linear vesting and a fee. Each
allocation is a leaf
`keccak256(keccak256(abi.encode(index, account, amount)))` — the
OpenZeppelin StandardMerkleTree encoding, so trees can be built with the
standard tooling. Claims verify against the root; payouts are
`amount × vested_fraction × (1 − fee)` in `Wad` arithmetic; `claimMulti`
settles many allocations in one multiproof.

The reproducible demo is the EVM test suite (`fe test`): it deploys the
contract with the independently computed 4-leaf tree from
`vectors/merkle_vectors.json`, funds it, claims leaf 0 with its
2-element proof, batch-claims leaves 0–1 and 0–3 with multiproofs, and
checks exact payouts under a 25% fee, a zero-vesting window, and a
partial-vesting window — plus double-claim, bad-proof, wrong-amount,
bad-flags, insufficient-balance, and over-100%-fee reverts.

To try it by hand against a local node (after `fe build`; verified with
Foundry 1.9.0 against anvil):

```sh
anvil &  # Foundry local node
FROM=0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
ROOT=0x8a0793ea3c3790c59a9d8fbde3c6fb9fe6ef4f4f13b4ec0c9e02f910024c07ad
# Deploy with root, no vesting window (0, 0), no fee:
ARGS=$(cast abi-encode "constructor(uint256,uint256,uint256,uint256)" $ROOT 0 0 0)
BYTECODE="0x$(cat out/MerkleDistributor.bin)${ARGS#0x}"
DIST=$(cast send --unlocked --from $FROM --create "$BYTECODE" --json \
  | jq -r .contractAddress)
# Fund it:
cast send --unlocked --from $FROM --value 5ether "$DIST" "fund()"
# Claim leaf 0 (0.25 ETH to 0x1111...1111) with its proof:
cast send --unlocked --from $FROM "$DIST" \
  "claim(uint256[],uint256,address,uint256)" \
  "[0x647dc7437e02fd27e50f72e3bb9edfb29349b80dba3beb9399e592eec7e26b50,0x04d015752dc2e032305c43ea9f20ffae4dcb3a7e3f8bfdaa4d095965f9c2b28f]" \
  0 0x1111111111111111111111111111111111111111 250000000000000000
# Batch-claim leaves 1 and 2 with one multiproof (proof [leaf0, leaf3],
# flags [false, false, true]):
cast send --unlocked --from $FROM "$DIST" \
  "claimMulti(uint256[],bool[],uint256[],address[],uint256[])" \
  "[0xe0afe6df674ec998ece90d140c5916b331289a76c258163da083e5c1c6403f7b,0x28333f592aaf420dc45328a4db42750e6e228bf3318ebbfcafaf9a51e6df52c3]" \
  "[false,false,true]" "[1,2]" \
  "[0x2222222222222222222222222222222222222222,0x3333333333333333333333333333333333333333]" \
  "[500000000000007919,750000000000015838]"
# Verify: balances, flags, and the Claimed events on the receipts.
cast balance 0x1111111111111111111111111111111111111111
cast call "$DIST" "isClaimed(uint256)" 1
cast call "$DIST" "totalClaimed()"
```

All values above come from `vectors/merkle_vectors.json` (the 4-leaf
tree). Note the multiproof for leaves {1, 2} is *not* the same shape as
for leaves {0, 1}: proof/flag pairs are per-subset — generate them with
`tools/gen_merkle_vectors.py` (or any OZ-compatible tooling) rather
than reusing another subset's proof; a mismatched multiproof reverts
with `invalid multiproof`, as it should.

## Test vectors: how they stay honest

The vectors are **not** generated by the Fe code, and they are not
trusted on first sight:

- **Merkle.** Keccak comes from two independent implementations
  (pycryptodome and eth_hash) that must agree on every hash. The tree
  builder is validated against the Fe stdlib's own fixture roots
  (2/3/8-leaf trees) before any vector is emitted. Every multiproof
  vector is validated by an independent literal Python port of
  OpenZeppelin's `processMultiProof`, which must reproduce the tree
  root; a Fe control-flow mirror additionally classifies tampered
  inputs as "returns false" vs "reverts". Coverage: all subsets of the
  4- and 8-leaf trees (exhaustive), structured + random subsets of the
  16-leaf tree, all single proofs, all leaf encodings, tampered
  proof/flag/leaf cases, and length/underflow reverts.
- **Fixed-point.** Expected values come from Python exact integer
  arithmetic. Where the library composes std primitives (power,
  square root), the generator replicates std's *documented* algorithms
  step for step (binary squaring with half-up steps; floor sqrt of the
  scaled radicand) — so those vectors test this library's composition,
  not a re-implementation of std internals. The rounding, aggregation,
  and growth semantics are specified independently in `src/fixedx.fe`
  and pinned by hand-picked edges (ties, zeros, `u256::MAX`, overflow
  boundaries) plus seeded random cases at three scales (D = 6, 18, 27).

Regenerating the vectors is deterministic (fixed seeds) and CI checks
that the committed generated files match the generators.

## Benchmarks (summary)

Full method, tables, and caveats: [BENCHMARKS.md](BENCHMARKS.md).

- Multiproofs beat repeated single proofs by **6.1x (n=8)** and
  **9.6x (n=16)** in gas on Fe; per-leaf cost falls 6.8x–10.9x as the
  batch grows. The OZ baseline shows the same pattern (4.0x / 5.5x).
- Distributor batch claims cost **31–37% less per claim** than single
  claims (the remainder is per-claim storage + transfer costs that
  batching cannot amortize).
- Rounding to nearest costs **~112–143 gas** over floor division.
- Honest caveat, measured: every external call to a Fe 26.6 contract
  pays ~22k gas of dispatcher/ABI overhead in this harness (a trivial
  `root()` read costs 21,800 gas). The deltas and ratios above are the
  meaningful figures; the absolute totals include that floor on the Fe
  side.

## Limitations

- **Multiproofs require perfect trees.** Like OpenZeppelin's
  implementation, `process_multi_proof` assumes a complete (power of
  two) tree and leaves in queue-consumption (index) order. Odd-leaf
  trees with promoted nodes do not admit multiproofs; single proofs
  (`std::evm::merkle::verify`) handle those. The empty-set case
  (`proof = [root]`, no flags, no leaves) returns the root, exactly as
  OZ does — callers must validate that leaves were actually supplied.
- **One leaf encoding per tree.** `leaf_from_words` (double hash over
  ABI words) and `leaf_from_packed` (single hash over packed encoding)
  produce different leaves; mixing encodings within one tree breaks
  verification. Pick one and document it.
- **`geometric_mean` rounds down twice** (floor `*`, then floor `sqrt`),
  with error under 2 raw units. It is not a correctly-rounded mean.
- **`pow_signed` with a negative exponent reverts** when the positive
  power underflows to zero raw (e.g. `0.5 ** -100`), because the
  reciprocal would divide by zero. `compound`/`discount` inherit std's
  half-up squaring rounding; they are not correctly-rounded exponentials.
- **`mul_div_nearest` reverts** on `d == 0` and on rounded-result
  overflow; use `checked_mul_div_nearest` when a `None` is preferable
  to a revert.
- **The distributor is a demo, not a production airdrop contract.** No
  owner, no root rotation, no rescue function, no reentrancy guard
  beyond checks-effects-interactions (state is updated before external
  calls). It pays out the chain's native currency only.
- **No formal verification.** Correctness rests on the vector suites
  and the OZ-port cross-validation, not on proofs.
- **Fe 26.6 toolchain notes (observed while building).** `fe test`
  cannot deploy a contract with attached value (deploy with zero value
  and fund via a payable message instead); the per-call gas overhead
  noted above is a toolchain property. Both are documented here so
  adopters are not surprised.

## Declaration

- **Pre-existing work:** none. All Fe code in `src/` was written fresh
  for this challenge.
- **Reused code:** none vendored. The library depends on the Fe
  standard library (`core`, `std`) as a language dependency and uses
  its public APIs. OpenZeppelin's `MerkleProof` was used as the
  *semantic reference* for the multiproof algorithm (reimplemented in
  Fe from its documented behavior, cross-validated by an independent
  Python port) and as the *benchmark baseline* (fetched by Forge, not
  copied into this repo). The starter repository
  (`fe-lang/builders-challenge-starter`) was used as a reference for
  project layout and CI shape only; no starter code is included.
- **AI assistance:** this entry was built with AI assistance (Muse, an
  AI agent, working from the author's brief). All code, vectors, and
  documentation were produced in that collaboration; every function is
  covered by independently computed test vectors as described above,
  and the benchmark numbers are measured, not generated.

## For the submission form

- **Short pitch.** Fe stdlib extensions that fill the two gaps every
  distribution and vesting contract hits: OpenZeppelin-compatible
  Merkle multiproofs + standard leaf encodings, and rounding-to-nearest
  / aggregation / growth math over std fixed-point types — 790 tests on
  independently computed vectors, gas benchmarks vs an OZ baseline.
- **Fe-specific highlight.** Const-generic trait extension over
  `UFixed<D>` (one implementation, every scale); a `ProofFlags` trait
  mirroring std's `ProofNodes` so multiproofs accept any mix of
  zero-copy calldata containers; effect-checked contract handlers;
  `const fn` leaf hashing over std's const keccak intrinsic; EVM tests
  with `#[test(should_revert)]` plus Foundry gas measurement of the
  compiled artifacts.
- **Demo.** The `MerkleDistributor` contract: deploy with a Merkle
  root, fund it, and claim with single proofs or batched multiproofs
  while vesting + fee math prices each payout. Runnable via `fe test`
  (EVM suite) and `bench/` (Forge gas benchmarks); see "Demo" above.

## License

MIT — see [LICENSE](LICENSE).
