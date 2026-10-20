# IpodRawDriveDumper

A read-only tool that makes a safe backup copy of an iPod's firmware. It **never writes to, restores, or flashes your iPod** — it only reads. There is no write path in the program at all; disk access is opened read-only and the only low-level queries are for disk size and geometry.

> ⚠️ **Work in progress.** This tool is under active development. It already works well for the models listed below, but features and model support are still being added, and version numbers change often. Please read the support list before relying on it, and always keep your original iPod and a known-good IPSW as backups.

## What works right now

| iPod model | Firmware dump over USB disk mode? | Notes |
|---|---|---|
| **iPod 3G** | ✅ Yes — dumped and IPSW-verified | PortalPlayer PP5002; same firmware layout, OSOS enters with `MOV PC, R0`; dump cross-checked against `iPod_2.2.3.ipsw` |
| **iPod Video 5G / 5.5G** | ✅ Yes — tested on real hardware | Full firmware dump + named sub-image extraction |
| **iPod Mini 2G** | ✅ Yes — tested on real hardware | PP5020 generation; full firmware dump + named sub-image extraction |
| **iPod 4G / Photo (Color)** | ✅ Yes — dumped and IPSW-verified | Same PP5020 firmware layout as the Mini; dump cross-checked against `iPod_11.1.2.1.ipsw` |
| **iPod Classic 6G/6.5G/7G** | ❌ No | Detected and identified, but the Classic only exposes its data partition over disk mode — firmware is not readable this way (needs DFU/wInd3x) |
| iPod Nano / Shuffle | ❌ Not supported | — |

Note on model names: over USB disk mode the iPod Mini, 4G, and Photo/Color are the same PortalPlayer PP5020 generation and expose byte-for-byte identical firmware fingerprints. The tool therefore labels them with the shared family name "iPod Mini / 4G / Photo (PortalPlayer PP5020)" rather than guessing which one it is — the dump itself is complete and correct regardless of the exact model.

What a successful dump gives you:

- A complete raw image of the firmware partition.
- Each named firmware sub-image extracted separately (the OS / `soso`, the updater / `dpua`, and any others present).
- SHA-256 hashes, the firmware directory, disk geometry, and a human-readable summary — so you can verify and inspect exactly what was captured.

### Verified on real hardware

Each of these was dumped over USB disk mode, with the raw image plus every extracted `!ATA` sub-image re-checked against the recorded SHA-256 hashes. For two models the dump was also cross-checked against the matching official IPSW firmware (see below):

- **iPod 3G** (PP5002) — dumped COMPLETE; OSOS entry `MOV PC, R0` (`0xE1A0F000`), build `PP5002DF-04.11-...` / `Tango(tm) Digital Media Platform`. **Cross-checked against `iPod_2.2.3.ipsw`:** the `dpua` updater is byte-for-byte identical, and the `soso` OS is bit-identical for its first 3,432,960 bytes (~99%), so the dump is confirmed to be the same firmware build.
- **iPod Video 5G/5.5G** — firmware partition captured and verified (2048-byte sector layout).
- **iPod Mini 2G** (PP5020) — `soso` (OS) and `dpua` (updater) extracted and verified.
- **iPod 4G / Photo (Color)** (PP5020) — full firmware partition (41,126,400 bytes) captured COMPLETE with `soso` and `dpua` extracted; raw image and both sub-images re-verified byte-for-byte against their hashes. **Cross-checked against `iPod_11.1.2.1.ipsw`:** the `dpua` updater is byte-for-byte identical and the `soso` OS is bit-identical for its first 5,387,776 bytes (~99%), confirming the same firmware build.

About the IPSW cross-check: the on-device OS is the *installed* copy, so the last ~1% of the OSOS image differs from the clean, packed OSOS inside an IPSW (flashed/runtime state vs. factory package). The updater (`dpua`) and the entire packed OSOS code region match exactly, which is what authenticates the dump as a genuine capture of that firmware build. This is a verification aid only — the tool still cannot rebuild a flashable IPSW.

What it does **not** do: it is not a restore tool and cannot rebuild a flashable IPSW. The on-device OS it captures is the installed copy, which differs from the clean packed images inside an official IPSW. Keep using official IPSW files for restoring.

## Download

The ready-to-run `IpodRawDriveDumper_vx.y.z.exe` is published **only on the [Releases page](https://github.com/giek2000/iPodDriveDumper/releases)** — it is intentionally not stored in the source repository. Download the latest `.exe` from there, or [build it yourself](#build).

## Quick start (GUI)

1. **Put your iPod into disk mode** and connect it with USB. (On iPod Video/Mini: toggle Hold on then off, hold Select + Menu to reboot, then immediately hold Select + Play to force disk mode.)
2. **Run the EXE as Administrator.** Reading raw disk sectors needs admin rights; the app will ask to elevate automatically.
3. Click **Refresh**, then pick your iPod from the **iPod** list. Only validated iPod firmware disks appear here, so you won't accidentally target the wrong drive.
4. Set **Files output** to a folder on your PC — for example `C:\iPodBackup`. **Never** choose the iPod's own drive letter.
5. (Optional) Tick **Extract sections** to save each firmware sub-image by name, and **Full partition** to capture the entire firmware partition including reserved space.
6. Click **Start Read-Only Dump** and wait for it to finish. When it's done, use **Open Output** to see the files and **Open Read Log** to review exactly what was read.

If anything looks wrong, nothing has been changed on the iPod — the tool only ever reads. Just disconnect and try again.

## Compatibility (technical detail)

Supported for raw disk-mode firmware dumping:

- **iPod 3G** (PortalPlayer PP5002) — same `{{~~` / `]ih[` / `!ATA` PortalPlayer layout, confirmed on a live iPod 3G (primary firmware at `0x7E00` with a mirror image later on the partition). Its OSOS does not begin with an ARM `B` branch like the later models; it enters with `MOV PC, R0` (`0xE1A0F000`). The validator accepts this entry instruction, so the 3G validates and dumps.
- **iPod Video 5G/5.5G** (PortalPlayer PP5022/PP5022C) — `{{~~` / `]ih[` / `!ATA` firmware layout. This model exposes its firmware partition as readable sectors over USB disk mode.
- **iPod Mini / 4G / Photo (Color)** (PortalPlayer PP5020) — same `{{~~` / `]ih[` / `!ATA` PortalPlayer layout, confirmed on a live iPod Mini 2G and a live iPod 4G Color. Their OSOS begins with the ARM branch `0xEA000079` (not the Video's `0xEA00007A`); the validator accepts any ARM-branch OSOS first word, so they validate and dump. These PP5020 models are not reliably distinguishable from one another over disk mode — all three carry the same `portalplayer` string and the same `PP5020AF-05.00-...` boot build string — so the tool reports the shared family name rather than claiming a specific model. Both a Mini 2G and a 4G Color have been dumped end to end and the raw image plus every extracted `!ATA` sub-image re-verified against the recorded SHA-256 hashes; the OSOS image size differs between them (the 4G Color's `soso` is larger than the Mini's), which is expected since the firmware build differs even though the family fingerprint is identical.

Recognized but not raw-dumpable over disk mode:

- **iPod Classic 6G/6.5G/7G** (Samsung S5L8702) — the Classic exposes only its FAT32 data partition over USB disk mode; its firmware partition is not presented as readable sectors. The tool detects an "iPod data partition only" device and explains this, rather than claiming a dump is possible. Raw firmware extraction on Classic requires DFU/wInd3x, not disk-mode reads. The bundled S5L8702 / `8702` IMG1 recognizer applies when an actual IMG1 firmware image is present in readable sectors (for example, a restored/extracted image), not to a stock Classic in disk mode.

Other iPod families (Nano, Shuffle) are not supported.

Read-only diagnostic commands (`probe`, `hexdump`) are provided to inspect a device's real on-disk bytes before any dump.

### Full-partition dump and sub-image extraction

For a complete firmware-partition image (useful for preserving beta firmware and for later IPSW recreation), use full-partition mode:

```powershell
IpodRawDriveDumper.exe dump --drive \\.\PhysicalDrive2 --output C:\Backups\miniFW --full-partition --extract-sections
```

- `--full-partition` dumps the entire MBR-declared firmware partition, not just up to the last `!ATA` section, so trailing/reserved space is preserved.
- `--extract-sections` writes every validated `!ATA` sub-image by name (`soso`/OSOS, `aupd`/`dpua` updater, `rsrc`, `hibe`, and any others present), each with its directory record, offsets, load/entry addresses, and stored checksum recorded in `metadata.json`.
- The raw image, the preserved MBR, the `]ih[` directory, per-section SHA-256 hashes, and geometry together form the inputs a separate packaging tool would need to rebuild an IPSW. This tool stays read-only and does not itself package or flash.

In the GUI, tick both "Full partition" and "Extract sections" before starting the dump. A device with two firmware images (primary + mirror) lists them separately by offset so each can be dumped.


Put the iPod in disk mode, keep it connected during scanning/dumping, and verify the selected physical disk. Administrator privileges are normally required. The final confirmation states that no write access is requested and no disk sectors are modified. Do not use this tool as a restore image generator.

This is also a standalone PySide6 GUI: use the **iPod** selector to choose a validated physical disk candidate and **Files output** to choose a host-side destination. The GUI includes Refresh, layout inspection, section extraction, explicit read-only confirmation, progress/cancel, and output/log actions.


To try it without any hardware, run the built-in fixtures from the directory that contains the `iPodRawDriveDumper` package folder (the repository root):

```powershell
python -m iPodRawDriveDumper inspect --fixture 2048 --json
python -m iPodRawDriveDumper dump --fixture 512 --output .\iPodRawDriveDumper\fixture-output --extract-sections --chunk-size 1048576
python -m iPodRawDriveDumper diagnostics --session .\iPodRawDriveDumper\fixture-output
```

The fixtures exercise both 512-byte and observed 2048-byte layouts without requiring a physical disk.

## Live use

```powershell
IpodRawDriveDumper_vx.y.z.exe scan --json
IpodRawDriveDumper_vx.y.z.exe inspect --drive \\.\PhysicalDrive1 --json
IpodRawDriveDumper_vx.y.z.exe dump --drive \\.\PhysicalDrive1 --output C:\Backups\iPodSession --extract-sections
```

No restore, patch, format, force-write, or equivalent command exists in this executable.

## Build

On Windows with the pinned build dependencies installed:

```powershell
python -m pip install -r requirements.txt
python build.py
```

The build script increments the patch version on each successful rebuild and produces `dist\IpodRawDriveDumper_vx.y.z.exe`.

The partition-boundary rule intentionally retains both MBR 512-byte interpretations and queried logical-sector geometry. A contradictory or unresolvable boundary is not accepted as a complete dump range.
