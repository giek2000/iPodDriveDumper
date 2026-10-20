# Implementation note

The live 5G/5.5G observations report a 2048-byte logical sector while MBR partition fields conventionally use 512-byte units. The dumper preserves both raw MBR-LBA-derived byte ranges and queried logical-sector interpretations in `metadata.json`. A firmware partition entry is selected only when its 512-byte interpretation is in bounds and contains all validated sections; missing or contradictory partition metadata is retained as a structural warning and cannot produce a complete default range.

The first release uses buffered reads. The no-buffering option is intentionally rejected until a dedicated sector-aligned allocator and complete subrange handling are validated. This avoids silently inheriting unsafe alignment behavior from older research scripts.
