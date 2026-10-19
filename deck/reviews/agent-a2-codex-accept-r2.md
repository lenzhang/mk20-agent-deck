PASS

No BLOCKER/MAJOR/MINOR findings in the scoped diff.

- **r1 MAJOR fixed:** deadline checks cover tiles/rows, finalization and JPEG encoding. The [compressed-frame regression]( /Users/dev/worktrees/mk20-agent-a1/deck/agent/tests/test_rfb.py:209) passed with/without sanitizers in 128/105 ms, leaving no output; it rejected the pre-r1 binary.
- **Kimi #3 fixed:** [des.c:38](/Users/dev/worktrees/mk20-agent-a1/deck/agent/src/des.c:38) wipes key/subkeys after output. DES known-vector output passed; disassembly confirms retained wipe calls.
- [agent_cli.c:39](/Users/dev/worktrees/mk20-agent-a1/deck/agent/src/agent_cli.c:39) reads Linux VmHWM in KiB. Warning-clean syntax checks passed; Linux runtime remains unverified on macOS.

Validation used existing binaries newer than the sources. No files edited.
