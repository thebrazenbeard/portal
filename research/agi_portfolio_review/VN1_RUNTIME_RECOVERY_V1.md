# VN1 — Isolated Granite local reviewer runtime repair (2026-10-22)

**Purpose:** restore an actually executing, physically separate small local reviewer on VERA-NODE-1 (Windows) for Vera Goal 1's AGI portfolio review. This is not a VeraMonoPilot model cutover, autonomously operating reviewer, or independent verdict on QVR.

## Evidence and selected executable

The existing installed Granite GGUF was already exactly SHA-256 verified, but the original Windows llama.cpp `b11827` binary crashed **before inference** with Windows `0xC0000005` / faulting `MSVCP140.dll`. System-wide MSVC library version inspected was `14.32.31332.0`. We isolated the fault to binary-compatibility; replacing model weights alone would have been inconclusive.

Two additional isolated official ggml-org/llama.cpp Windows builds were staged under `C:\Users\Vera\vera-review-sandbox\bin\`:

| Build | Result |
| --- | --- |
| `b11827` cpu-x64 | `0xC0000005`, unusable |
| `b9000` cpu-x64 | `0xC0000005` from `--version`; downloaded archive SHA-256 matched upstream GitHub release `8294e287933d3212aa93a32e1ceb800722bede14d9692d405679d4ba77cf05db` |
| **`b4179` win-noavx-x64** | **`--version` returns exit 0**, reports version 4179 MSVC 19.29.30157; model inference exit 0 |

Local verified `b4179` archive SHA-256: `8f395384decb5ff5a7eeb95ab294b77f5b2c101d1fc3ce63cb7942e6705c3499`; `llama-cli.exe` SHA-256: `6ca5a993a9eab29f59af1c45a166b1500833ad08996f816b4ec24f7ec00b95ac`. Model is `ibm-granite_granite-3.3-2b-instruct-Q4_K_M.gguf` exact SHA-256 `555b91485955bc96eb445b57dd4bbf8809aa7d8cce7c313f4f8bc5b2340896b4`, from `bartowski/ibm-granite_granite-3.3-2b-instruct-GGUF` at commit `4e06dce39d4c5fcf65ca1cc69b92962e411d38e1`.

An isolated CPU test with the pinned `b4179` binary returned `VERA_REVIEW_READY` as part of generated text for prompt `Reply exactly VERA_REVIEW_READY.`, **exit 0**, approximately **23.9 s** wall time, CPU decode near **1.1 tokens/s** at four threads. Extra text followed the marker, so do not treat it as a perfect-format compliance test.

Machine-local selected profile: `C:\Users\Vera\vera-review-sandbox\evidence\selected_reviewer_profile.json`. This file is **local research configuration**, not a selected `QualifiedVeraRuntime` cognition route. Existing Qwen2.5 partial/alternate local files are not promoted.

## Alternative model research

Microsoft `Phi-4-mini-reasoning` and `DeepSeek-R1-Distill-Qwen-1.5B` were checked in Hugging Face source metadata; both list **MIT** licenses. They were not downloaded as replacements because Granite now works on the older Windows executable. Phi-4-mini is larger; DeepSeek-Distill-Qwen is not fully independent of Lappy's Qwen family. Neither alternative was tested.

## Independent review acceptance boundary

A model stored on independent VN1 hardware is not necessarily an *independently qualified* source reviewer. Each completed review must bind the exact source Git commit and blob, model bytes, executable hash/version, prompt and response hashes, completion exit, and source-verifiable negative tests; compare blind first-round reports before sharing peers' findings. CPU capacity is limited—prefer short exact-source excerpts to whole-repo context dumps. Do not assume sustained background operation.

No Windows machine-wide Visual C++ runtime installation, Service Control Manager modification, `VeraMonoPilot` repair/restart, provider route cutover, VeraMesh mutation, credential/trust or canonical memory write was made.
