// SPDX-License-Identifier: MIT
// Solidity baseline for the gas benchmarks: thin wrappers over
// OpenZeppelin's MerkleProof, measured with the same inputs as the Fe
// harness. OpenZeppelin is fetched by `forge install` (see BENCHMARKS.md);
// no OZ source is vendored in this repo.
pragma solidity ^0.8.24;

import {MerkleProof} from "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";

contract OzVerifier {
    function verify(bytes32[] memory proof, bytes32 root, bytes32 leaf) external pure returns (bool) {
        return MerkleProof.verify(proof, root, leaf);
    }

    function multiProofVerify(
        bytes32[] memory proof,
        bool[] memory proofFlags,
        bytes32 root,
        bytes32[] memory leaves
    ) external pure returns (bool) {
        return MerkleProof.multiProofVerify(proof, proofFlags, root, leaves);
    }
}
