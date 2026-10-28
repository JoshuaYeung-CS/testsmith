# Demo video: testsmith (target 2:45, hard limit 3:00)

Setup before recording: terminal at a large font (18pt+), dark theme, window about 120 columns wide; an editor with `examples/buggy_inventory/stock.py` open; the repo's README open in a browser tab. Clear the terminal. No music. Speak over a screen recording.

Use a live run if you have one (`testsmith examples/buggy_inventory`). If you show the replay instead, say "this is a recorded run" when it starts and keep the `--replay` flag visible.

| Time | On screen | Say |
|---|---|---|
| 0:00-0:15 | README title and the pipeline figure | "Test-writing tools have a bad habit: when a test fails, they change the test until it passes. That turns real bugs into green checkmarks. testsmith asks a different question: is the test wrong, or is the code?" |
| 0:15-0:35 | `examples/buggy_inventory/stock.py`, highlight the `needs_reorder` docstring ("at or below") and the `<` | "Here's a small inventory library with a thin test suite, about half covered. Its docstrings are the spec. Some of the code doesn't match them." |
| 0:35-0:50 | Terminal: `testsmith --check` | "Every model call goes to Nebius Token Factory through its OpenAI-compatible API. Two NVIDIA Nemotron models: Lightning writes tests fast and cheap; Ultra, the big reasoning model, does the one judgment that has to be right." |
| 0:50-1:25 | Terminal: `testsmith examples/buggy_inventory`, let it run; pause on "round 1: 17 passed, 5 failing" and the triage line | "It reads the package, writes tests, and runs them on a throwaway copy: timeouts, no network, no secrets. Five tests fail. Rules settle the ones that can only be the test's fault, like calling a function with an argument it doesn't have. The rest go to Nemotron Ultra, which has to quote the promise the code breaks before I'll call it a bug." |
| 1:25-1:45 | Rounds 2 and 3 scroll by; coverage climbs to 100% | "Wrong tests get repaired and re-run. When nothing new fails, it sends the uncovered line numbers back and asks for tests that reach them. Coverage goes from 52 to 100 percent." |
| 1:45-2:20 | Open `report.md` (preview), scroll to "Likely bugs": the `remove(-5)` finding with its promise, assertion and repro output "on hand after remove(-5): 45" | "Three bugs, each with evidence: the docstring sentence it violates, the failing assertion, and a standalone repro that ran in the sandbox. Removing minus five units adds five. An item exactly at its reorder point is never reordered. A tax total truncates instead of rounding. Each one failed the same way on every run." |
| 2:20-2:35 | `test_buggy_inventory_testsmith.py`, highlight an `xfail(strict=True)` line; then run `pytest` on it: green | "The test file is green today. Bug tests are strict expected failures, so the day someone fixes the bug, CI tells them to remove the marker." |
| 2:35-2:45 | Model usage table at the bottom of the report | "Every call is logged: tokens, latency, retries, cost. testsmith is MIT licensed, and you can try it without an account using the recorded replay in the repo." |

Cut list if you run long: drop the `--check` shot (0:35-0:50) and say the model line over the run instead.

Do not show: `.env`, your API key, the Token Factory console with the key visible, browser tabs with personal accounts.
