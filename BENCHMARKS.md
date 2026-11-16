# Benchmarks

All numbers below are measured gas from real EVM execution (Foundry),
not estimates. The Fe code measured is the exact artifact `fe build`
produces: the benchmark generator embeds the compiled bytecode into the
Forge test, so the numbers always match the current source.

## Method

- **Fe side.** `src/bench_harness.fe` exposes the library functions as
  external calls (`verifySingle`, `verifyMulti`, `mulDivNearest`,
  `mulDivFloor`, `wadMulNearest`, `wadMulFloor`, `powSigned`, `compound`,
  `leafFromWords`). `src/lib.fe` (`MerkleDistributor`) is measured
  end-to-end: deploy, fund, claim, claimMulti.
- **Solidity baseline.** `bench/src/OzVerifier.sol` wraps OpenZeppelin
  5.7.0 `MerkleProof.verify` / `multiProofVerify`, compiled with solc
  0.8.24, optimizer on, 200 runs. No OZ source is vendored; it is fetched
  by `forge install` (or a plain clone into `bench/lib/`).
- **Measurement.** `gasleft()` deltas around a single external call with
  identical calldata on both sides. Each measured call is preceded by an
  identical warm-up call, so the target account is warm and the delta is
  execution + calldata, not cold-account access. Distributor claims are
  measured on a freshly deployed, freshly funded contract (cold storage
  writes and first-time transfers to fresh accounts — the realistic
  first-claim cost).
- **Inputs.** All Merkle inputs come from `vectors/merkle_vectors.json`
  (the same independently computed vectors the tests use). Fixed-point
  inputs are listed in `tools/gen_bench.py`.
- **Environment.** `fe 26.6.0`, Foundry 1.9.0, EVM version Cancun,
  Linux x86_64. Reproduce with:

```sh
fe build
python3 tools/gen_bench.py
cd bench
forge install foundry-rs/forge-std OpenZeppelin/openzeppelin-contracts
forge test -vvv
```

## Results

### The fixed per-call overhead (read this first)

A trivial Fe call costs ~22k gas before it does any work:

| Call | Gas |
|---|---|
| `MerkleDistributor.root()` (one storage read) | 21,800 |
| `MerkleDistributor.isClaimed(0)` (one mapping read) | 26,551 |
| `mulDivFloor` on `(1, 1, 2)` (minimal compute) | 22,370 |

This ~22k floor is the Fe 26.6 dispatcher + ABI decoding + runtime
prologue as observed through this harness; it is a property of the
toolchain, not of this library's logic. **The meaningful comparisons
are therefore the deltas and the batching ratios**, not the absolute
totals. Every Fe number below includes the same ~22k floor.

### Single-proof verification (stdlib `merkle::verify` via the harness)

| Tree size | Proof length | Fe gas | OZ `verify` gas |
|---|---|---|---|
| 2 | 1 | 24,337 | 6,504 |
| 4 | 2 | 25,075 | 6,902 |
| 8 | 3 | 25,802 | 7,276 |
| 16 | 4 | 26,540 | 7,674 |

Marginal cost per additional proof element (one sorted-pair hash +
decoding): **~734 gas (Fe)** vs **~390 gas (OZ)**. The Fe stdlib verifier
costs roughly 2x the OZ verifier per hash; the rest of the gap is the
fixed per-call overhead above.

### Multiproof verification (this library vs OZ)

| Case | Fe gas | Fe gas / leaf | OZ gas | OZ gas / leaf |
|---|---|---|---|---|
| n=8, k=1 | 28,805 | 28,805 | 10,756 | 10,756 |
| n=8, k=2 | 28,839 | 14,419 | 10,769 | 5,384 |
| n=8, k=4 | 30,161 | 7,540 | 11,741 | 2,935 |
| n=8, k=8 | 34,054 | 4,256 | 14,656 | 1,832 |
| n=16, k=1 | 30,055 | 30,055 | 11,726 | 11,726 |
| n=16, k=2 | 30,077 | 15,038 | 11,739 | 5,869 |
| n=16, k=8 | 35,302 | 4,412 | 15,628 | 1,953 |
| n=16, k=16 | 44,270 | 2,766 | 22,453 | 1,403 |

Batching pays off in both implementations, and **relatively more on Fe**,
because one call amortizes the fixed overhead:

| Batch | Fe: k singles vs 1 multiproof | OZ: k singles vs 1 multiproof |
|---|---|---|
| n=8, k=8 | 206,416 vs 34,054 — **6.06x cheaper (83.5% saved)** | 58,208 vs 14,656 — 3.97x cheaper (74.8% saved) |
| n=16, k=16 | 424,640 vs 44,270 — **9.59x cheaper (89.6% saved)** | 122,784 vs 22,453 — 5.47x cheaper (81.7% saved) |

Per-leaf cost falls 6.8x (n=8) and 10.9x (n=16) from k=1 to a full-tree
batch on Fe. The multiproof also shrinks calldata: the n=16 full batch
needs zero proof words, versus 4 words per leaf for single proofs.

### Distributor claims (end-to-end, first claim, fresh accounts)

| Call | Gas | Gas / claim |
|---|---|---|
| `claim` (n=8, proof of 3) | 106,734 | 106,734 |
| `claimMulti`, k=4 | 293,411 | 73,352 (−31.3% vs single) |
| `claimMulti`, k=8 | 536,992 | 67,124 (−37.1% vs single) |

The dominant costs here are the four-figure fixed overhead plus, per
claim, a cold storage write (20,000) and a first transfer to a fresh
account (~36,600 with cold access + value + new-account costs). Batching
amortizes the call overhead and the shared proof verification; it cannot
amortize the per-claim storage and transfer costs, which is why the
per-claim saving (31–37%) is smaller than for pure verification.

### Fixed-point arithmetic (rounding overhead)

| Operation | Gas | Delta vs floor |
|---|---|---|
| `mulDivNearest` (big inputs) | 22,827 | +112 |
| `mulDivFloor` (big inputs) | 22,715 | — |
| `mulDivNearest` (tie `1/2`) | 22,513 | +143 |
| `mulDivFloor` (tie `1/2`) | 22,370 | — |
| `mulDivNearest` (near-`u256::MAX`) | 23,901 | +112 |
| `mulDivFloor` (near-`u256::MAX`) | 23,789 | — |
| `wadMulNearest` (1.5 × 2.5) | 22,469 | +112 |
| `wadMulFloor` (1.5 × 2.5) | 22,357 | — |

Rounding to nearest costs **~112–143 gas** over floor division: one
`mulmod` for the remainder, one comparison, one conditional increment.
The absolute totals are dominated by the ~22k per-call floor.

| Operation | Gas |
|---|---|
| `powSigned(1.05, 100)` | 24,759 |
| `powSigned(2, 64)` | 28,764 |
| `powSigned(2, -10)` | 28,720 |
| `compound(1000, 5%, 10 periods)` | 23,987 |
| `leafFromWords` (double keccak) | 22,806 |

### Sizes and deployment

| Artifact | Runtime bytes | Deploy gas |
|---|---|---|
| `BenchHarness` | 2,204 | 567,631 |
| `MerkleDistributor` | 2,935 | 760,968 |

Deploy gas includes 200 gas per runtime byte of code-deposit cost
(440,800 and 587,000 respectively) plus init-code execution.

## Honest caveats

- The Fe-vs-OZ absolute gap is dominated by the Fe 26.6 per-call
  overhead (~22k), not by this library. Do not read the totals as "Fe is
  3.5x slower at hashing": the per-hash marginal cost is ~2x, and the
  batching ratios favor Fe.
- OZ `verify`/`multiProofVerify` take `memory` arrays (calldata is
  copied to memory by the Solidity ABI decoder); the Fe harness takes
  `DynArray` views over calldata. Both are the idiomatic shapes for
  their language, and both are measured with identical calldata.
- Gas numbers are for the Cancun EVM version in Foundry 1.9.0. Other
  EVM versions and clients may differ slightly; the ratios are stable.
- The multiproof gas figures assume valid proofs. Reverts cost the gas
  spent up to the failing assert.
