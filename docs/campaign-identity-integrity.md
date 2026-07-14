# Campaign Identity Integrity Requirement

## Evidence gap

The campaign manifest command accepts duplicate channel, asset, copy, and format identifiers. Because variant IDs are derived from those values, duplicate inputs can produce duplicate variant IDs while `variant_count` still counts every row. A commercial reviewer then cannot address, approve, reject, retry, or audit one variant unambiguously.

This is an internal product requirement based on the current runnable manifest workflow and its validation behavior. It is not customer evidence.

## Scoped requirement

`campaign-manifest` must reject a campaign input when any of these identity dimensions contains a duplicate:

- `campaign.channels`
- `assets[].id`
- `copy_variants[].id`
- `formats[].id`

Identity matching is exact and case-sensitive in this version. The command must report the duplicated field before writing a manifest. Valid inputs and deterministic checksums must remain unchanged.

## Acceptance check

Automated regression coverage must demonstrate that:

1. each duplicate identity dimension is rejected with a field-specific error;
2. no output manifest is written for invalid input;
3. the existing valid example still produces 16 unique variant IDs; and
4. two runs over the valid example produce the same manifest checksum.

The measurable product signal is `manifest_identity_collision_rate`, defined as duplicate variant IDs divided by generated variants. Owner: Product Engineer. Independent review capability: Independent Quality Reviewer, invoked on demand. Cadence: every manifest build and CI regression run. Required threshold: `0` for every accepted manifest. Data source: generated manifest `variants[].id` and `variant_count`.
