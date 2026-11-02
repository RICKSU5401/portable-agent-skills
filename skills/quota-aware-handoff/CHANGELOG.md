# Public candidate 0.1.0

- Generalized the existing quota/checkpoint/one-attempt dispatcher implementation
  into a portable directory; personal model policies, machine paths and live
  project/usage history are excluded.
- Preserved quota reserves, unknown/stale-data stops, immutable checkpoints,
  pinned config/unit bytes, ACK/permit validation and retained recovery locks.
- Made dispatcher provider identifiers configurable. Required Fast off and
  rejected its explicit enable flag; examples are disabled and contain unknown
  balances, costs and model placeholders.
- Added a read-only return inspector and synthetic round-trip/tamper tests based
  on the later return-handoff protocol. The hosted protocol was readable but its
  Python source was unavailable in the preparation environment; this inspector
  is a new implementation of that documented behavior, not a claim of identical
  code to the hosted personal version.
- Kept the existing helper regression tests. The old global installer is outside
  this extracted module; use repository-level install/update/remove instructions.
- Reject duplicate JSON fields and nonfinite/overflowing JSON numbers at both
  file and stdin quota normalization/admission boundaries; an ambiguous export
  cannot conceal an exhausted balance.

The local source did not declare a semantic version or include a license file.
Source identity and comparison hashes are recorded in the private preparation
audit. This candidate is distributed under the repository owner's approved MIT
license; a copy is included in this directory for standalone installation.
Prior personal publication and prior platform tests are not evidence that this
public candidate was published or tested on those platforms.
