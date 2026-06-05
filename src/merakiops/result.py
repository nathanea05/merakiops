from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from merakiops.action import Action


@dataclass
class Mismatch:
    """A single action whose live resource state did not match the intended change.

    Attributes:
        action:     The Action that was verified.
        mismatches: A dict mapping each camelCase API field that differed to
                    {"expected": <intended value>, "actual": <live value>}.

                    For destroy operations, the key is "existence":
                    {"expected": "destroyed", "actual": "still exists"}

    Example:
        for mismatch in result.mismatched:
            print(mismatch.action.resource)
            for field, diff in mismatch.mismatches.items():
                print(f"  {field}: expected {diff['expected']}, got {diff['actual']}")
    """

    action: Action
    mismatches: dict[str, dict[str, Any]]


@dataclass
class VerifyResult:
    """The outcome of verifying one or more action batches.

    Returned by ActionBatch.verify(), ActionBatch.verify_many(), and
    ActionBatch.run().

    Attributes:
        verified:      Actions where every body field matched the live resource.
        mismatched:    Actions where one or more fields did not match. Each
                       entry is a Mismatch object with .action and .mismatches.
        unverifiable:  Actions that could not be checked — source_obj was not
                       stored, the model type is not in the verify registry, or
                       an API error occurred during the bulk fetch.
        batch_errors:  Error strings returned by Meraki for individual actions
                       that failed during batch execution. These come from
                       batch.errors and explain why Meraki rejected specific
                       actions, independent of the field-level comparison above.

    Example:
        result = ActionBatch.run("123456", actions)

        print(f"Verified:     {len(result.verified)}")
        print(f"Mismatched:   {len(result.mismatched)}")
        print(f"Unverifiable: {len(result.unverifiable)}")

        for mismatch in result.mismatched:
            print(mismatch.action.resource, mismatch.mismatches)

        if result.batch_errors:
            print("Meraki execution errors:", result.batch_errors)
    """

    verified: list[Action]
    mismatched: list[Mismatch]
    unverifiable: list[Action]
    batch_errors: list[str]

    def __repr__(self) -> str:
        return (
            f"VerifyResult("
            f"verified={len(self.verified)}, "
            f"mismatched={len(self.mismatched)}, "
            f"unverifiable={len(self.unverifiable)}, "
            f"batch_errors={len(self.batch_errors)})"
        )
