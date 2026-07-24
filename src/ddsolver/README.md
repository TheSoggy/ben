# DDS solver (`ddsolver`)

BEN's double-dummy solver wrapper, using the `ctypes`-loaded DDS 2.9 library
(`dds.dll` / `libdds.so` / `libdds.2.9.0.dylib`). On macOS the solve runs in a
subprocess (`dds_subprocess.py`) to isolate DDS crashes.

## Benchmarking: record a real session, replay it

`ddsrecorder.py` + `ddsreplay.py` turn a played session into a repeatable DDS
performance test. Every DDS call BEN makes is written to a JSON Lines file with
its **full input and output**; the replayer re-issues those calls, times them,
and checks the answers still match.

Benchmarking this way rather than on generated deals matters because DDS runtime
is dominated by *which* calls BEN makes — the trick-1 `solutions=1` batches for
bidding cost far more than the trick-11 ones for card play, and their mix is not
easy to guess. In a sample 16-deal session, trick 1 was **81 % of all DDS time**.

### Record

Recording is off unless you ask for it with `--ddsrecord`, accepted by
`game.py`, `gameserver.py`, `gameapi.py` and `table_manager_client.py`. The
value is a file or a directory (a directory gets one file per process, so
several BEN processes can record at once); a `.gz` suffix compresses.

```bash
python game.py --boards mysession.pbn --auto True --ddsrecord /tmp/dds-rec.jsonl
```

Where there is no command line to reach — Docker, frozen builds — the
`BEN_DDS_RECORD` environment variable does the same thing. `--ddsrecord` wins if
both are given.

The entry points arm the recorder right after parsing arguments, before any
`DDSolver` is constructed, so no call is missed. The header record is written
lazily with the first DDS call, which is how it can carry the DDS version, mode
and thread count that only become known later.

Calls are tagged with the board being played in `game.py` (so also
`gameserver.py`) and `table_manager_client.py`. `gameapi.py` is **not** tagged —
it serves requests concurrently under gevent, so a single "current board" would
mis-attribute interleaved requests. Its records still carry the complete input
and replay fine, just with `board: null`.

Records are flushed per line, so a session ended with Ctrl-C still yields a
usable file; the replayer skips a truncated final line.

Expect roughly 4 MB per 800 calls (200-sample batches dominate). Recording adds
the cost of serialising those batches, so **the recorded `ms` are not a clean
benchmark** — they are there for reference and appear in the report as
`rec ms/call`. The replay timings are the ones to compare.

### Replay

```bash
# straight benchmark, best of 3
python src/ddsolver/ddsreplay.py /tmp/dds-rec/dds-12345.jsonl --repeat 3

# what does the recording contain? (does not run DDS)
python src/ddsolver/ddsreplay.py rec.jsonl --list

# tune dds_max_threads for this machine
python src/ddsolver/ddsreplay.py rec.jsonl --threads 8 --threads 16 --threads 32

# is a DDS rebuild / a dds_mode change faster, and still correct?
python src/ddsolver/ddsreplay.py rec.jsonl --json new.json --baseline old.json
python src/ddsolver/ddsreplay.py rec.jsonl --dds-mode 2
```

The report breaks time down by `purpose` (`bid`, `lead`, `play`, `claimcheck`,
`claimtricks`, `contract`, `par`) and, with `--tricks`, by trick number. Other
useful filters: `--purpose`, `--board`, `--solutions`, `--min-trick`,
`--max-trick`, `--limit`, `--no-par`.

`--json` writes a machine-readable result that a later run can `--baseline`
against; the exit status is non-zero if any call returned something other than
what was recorded, so it can be used as a CI check that a DDS change is
behaviour-preserving.

Replay calls `solve_helper` / `_calculatepar_impl` rather than the public
`solve` / `calculatepar`, so it measures DDS itself and never re-triggers the
recorder.

**On repeatability:** with `dds_mode=1` DDS reuses a transposition table across
consecutive solves when the trump suit and card distribution are close, and DDS
schedules boards onto its internal threads nondeterministically, so a single run
carries a few percent of noise. Use `--repeat` (the best run is reported) and
`--warmup`.
