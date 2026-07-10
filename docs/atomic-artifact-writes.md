# Atomic Artifact Write Control

## Reliability gap

Local JSON artifacts were written directly to their final path. A process interruption,
full filesystem, or write error could therefore leave a truncated file at a path that
looks complete. Campaign review and audit consumers could mistake that partial file for
a valid PixWeave artifact.

## Bounded control

All local backend and campaign-manifest JSON output now uses the shared `write_json`
operation. It serializes before opening a file, writes and flushes a temporary file in
the destination directory, synchronizes its contents, and atomically replaces the final
path. A failed replacement leaves any prior artifact unchanged and removes the temporary
file. This protects file integrity on the local filesystem; it does not claim durable
remote storage or recovery from hardware failure.

## Regression evidence

`test_json_artifact_write_preserves_existing_file_when_replace_fails` creates an existing
artifact, injects a replacement failure, and verifies both that the original bytes remain
unchanged and that no temporary file is abandoned. Existing manifest and backend tests
exercise successful writes through the same operation.