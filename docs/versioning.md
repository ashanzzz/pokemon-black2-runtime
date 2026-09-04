# Runtime component versioning

The authoritative registry is `backend/black2/runtime/versions.py`.  A change
must update that registry before a component advertises a new capability.

## Version rules

- Runtime components use semantic versions: major for breaking API/process
  changes, minor for backward-compatible capabilities, and patch for compatible
  corrections.
- Protocol and exported-data schemas keep independent `/vN` identifiers.  An
  application release does not change a schema version unless the serialized
  contract changes.
- The live version endpoint is `GET /api/v1/runtime/versions`.  It reports both
  `expected_version` and `observed_version`; `mismatch` and `unavailable` are
  explicit states and must not be presented as compatible.
- The BizHawk Bridge observed version comes from its live handshake.  Static
  source declarations are not sufficient to mark the live bridge compatible.
- Capabilities must continue to be backed by registered handlers.  A version
  bump without the matching handler is rejected until an end-to-end check
  passes.

## Release checklist

1. Update the applicable component or schema constant in the registry.
2. Update its capability declaration and implementation together.
3. Run component tests and query `/api/v1/runtime/versions` against a clean
   runtime.
4. Confirm the monitor shows no unexpected `mismatch` state.
5. Record the version and verification result in a TEST REPORT.

