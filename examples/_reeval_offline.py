"""One-off: re-score already-saved raw transcripts with the fixed parser,
without spending any new API calls. Not part of the permanent test suite."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from validate import evaluate_run, parse_transcript, print_report  # noqa: E402

RESULTS_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if RESULTS_DIR is None:
    candidates = sorted((Path(__file__).parent / ".results").iterdir())
    RESULTS_DIR = candidates[-1]

print(f"Re-scoring transcripts under: {RESULTS_DIR}\n")

all_results = []
for example_dir in sorted(RESULTS_DIR.iterdir()):
    if not example_dir.is_dir():
        continue
    expected_path = Path(__file__).parent / example_dir.name / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    run_results = []
    for stdout_file in sorted(example_dir.glob("run-*.stdout.jsonl")):
        i = int(stdout_file.stem.split("-")[1].split(".")[0])
        stdout = stdout_file.read_text(encoding="utf-8")
        task_calls, final_text = parse_transcript(stdout)
        result = evaluate_run(task_calls, final_text, expected)
        result["run"] = i
        run_results.append(result)

    all_results.append({"example": example_dir.name, "expected": expected, "runs": run_results})

ok = print_report(all_results)
sys.exit(0 if ok else 1)
