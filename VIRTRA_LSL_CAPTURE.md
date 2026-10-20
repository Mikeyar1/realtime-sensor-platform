# Raw VirTra LSL capture

The [capture tool](tools/virtra_lsl_capture.py) records every channel value and LSL timestamp from VirTra event streams into a JSONL file. It runs separately from LabReview's scored session database.

From the repository root, start recording before the scenario:

```powershell
.\backend\.venv\Scripts\python.exe .\tools\virtra_lsl_capture.py record .\data\lsl_captures\my-session.jsonl
```

Press Ctrl+C after the scenario. Use a new output filename for each recording because `record` overwrites an existing file. By default it selects the native `V_300` stream and streams typed `VirTraEvents` or `VirTra Events`. Add `--all-streams` to include physiology and other LSL streams, or `--duration 120` to stop after 120 seconds. The command reports how many samples and streams it saved; zero samples returns a nonzero exit code.

To replay a capture into LSL for local testing:

```powershell
.\backend\.venv\Scripts\python.exe .\tools\virtra_lsl_capture.py replay .\data\lsl_captures\my-session.jsonl
```

`replay` also accepts `--speed`, `--start-delay`, and `--from-seconds`. `export-custom CAPTURE OUTPUT` converts a capture for the existing custom-event generator GUI. Use `--help` on each subcommand for its options.
