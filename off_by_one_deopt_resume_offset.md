# Off-by-one deopt resume offset

## Summary

When the CinderX HIR inliner inlines a callee that is called through a
**two-code-unit static invoke** (`EXTENDED_OPCODE` + inner word), and that
inlined callee later deopts, the caller's frame is resumed **one code unit
short of the next instruction** — landing on the second half of the invoke
pair. The interpreter then decodes that trailing word as a plain CPython
opcode and executes it against a stack that was never set up for it.

In DeltaBlue that trailing word decodes as `MATCH_KEYS`, which reads a
nonexistent stack slot and dereferences `NULL` → `SIGSEGV`.

This is a CinderX JIT bug, not a detyper bug and not a hardware-counter
issue. It reproduces on the **unmodified, fully typed** benchmark.

Supersedes `delta_blue_perf_crashes.md`, whose central claim — that the crash
requires `xctrace` counter attachment — is false.

## The defect

`_cinderx/cinderx/Jit/deopt.cpp:181-185`, in `reifyFrameImpl`:

```cpp
frame->instr_ptr += cause_instr_idx;                                  // = 2, base offset, correct
if (&frame_meta != &meta.innermostFrame()) {
  // If we're not the inner most frame then we're always deopting
  // after the instruction that executed
  frame->instr_ptr += inlineCacheSize(code_obj, cause_instr_idx) + 1; // + 0 + 1 = 3  ← mid-instruction
}
```

A caller frame holding an inlined callee is advanced "past the call" by a
hardcoded `+1` code unit. That is right for a one-unit call and wrong for the
two-unit `EXTENDED_OPCODE` encoding CinderX uses for static invokes.

`cause_instr_idx` itself is correct: `Jit/hir/builder.cpp:804` sets
`cur_instr_offs = bc_instr.baseOffset()`, i.e. the index of the
`EXTENDED_OPCODE` prefix.

The correct next-instruction arithmetic already exists at
`Jit/bytecode.cpp:190`:

```cpp
BCOffset BytecodeInstruction::nextInstrOffset() const {
  return BCOffset{
      opcodeIndex() + inlineCacheSize(code_, opcodeIndex().value()) + 1};
}
```

`opcodeIndex()` skips the `EXTENDED_OPCODE` prefix (`bytecode.cpp:55-63`), so
it produces index 4 where the deopt path produces 3. The deopt path simply
does not use it. Note the second, subtler half of the bug: the deopt path also
asks for `inlineCacheSize` at the *prefix* index rather than at the real
opcode's index, so any inline cache on the invoke is mismeasured too.

## Observed failure

Both symptoms trace to the same call site, `planner = recreate_planner()` in
`chain_test`:

| Variant | JIT + inliner | Result |
|---|---|---|
| Fully erased (`68719476735`) | on | `SIGSEGV` (rc 139) |
| Fully erased | inliner off | clean, 0.27 s |
| Fully erased | JIT off | clean, 1.50 s |
| **Original, fully typed** | on | `StaticTypeError: extract_plan_from_constraints expected 'Planner' for argument self, got 'tuple'` |
| **Original, fully typed** | inliner off | clean, 0.21–0.28 s |

The typed variant's bogus `tuple` argument is the same corruption, surviving
as a wrong value rather than a fatal dereference.

## Evidence

**1. Tracing is irrelevant.** The erased mask crashes with no `xctrace`
anywhere: 3/3 runs under `profile_compiled.py`, and also under
`run_compiled.py`. The control in the earlier writeup was not comparing the
same execution.

**2. It is the inliner, alone.** With the JIT on and one pass disabled at a
time:

| Flag | Result |
|---|---|
| `CINDERX_JIT_ENABLE_HIR_INLINER=0` | clean |
| `CINDERX_JIT_SIMPLIFY=0` | crash |
| `CINDERX_JIT_GUARD_TYPE_REMOVAL=0` | crash |
| `CINDERX_JIT_PHI_ELIM=0` | crash |
| `CINDERX_JIT_DYNAMIC_COMPARISON_ELIM=0` | crash |

**3. Minimal JIT list.** Delta-debugging `CINDERX_JIT_LIST_FILE` from all 66
functions reduces to 4, each of which is necessary:

```
__main__:chain_test
__main__:recreate_planner
__main__:get_planner
__main__:Plan.execute
```

`CINDERX_JIT_DEBUG_INLINER=1` shows exactly one successful inline in the whole
program:

```
Inlining function __main__:recreate_planner into __main__:chain_test
```

Everything else is rejected ("not preloaded", "non-function").

**4. The faulting state.** Under lldb:

```
stop reason = EXC_BAD_ACCESS (code=1, address=0x8)
  frame #0: Python`_PyObject_GetMethod + 44        ; ldr x23, [x0, #0x8], x0 = 0x0
  frame #1: Python`_PyEval_MatchKeys + 100
  frame #2: _cinderx.so`Ci_EvalFrame
  frame #4: _cinderx.so`jit::codegen::resumeInInterpreter at gen_asm.cpp:361
```

Reading the reified `_PyInterpreterFrame`: `co_qualname` is `chain_test`,
`co_firstlineno` 421, and `instr_ptr - co_code_adaptive` is **6**.

**5. What lives at offset 6.** Disassembling `chain_test` as the static
compiler emitted it:

```
   0 RESUME
   2 NOP
   4 EXTENDED_OPCODE          ← invoke of recreate_planner starts here
   6 MATCH_KEYS               ← trailing word of that invoke; where deopt resumed
   8 STORE_FAST      planner
```

Offset 6 is not an instruction. `2 + inlineCacheSize(code, 2) + 1 = 3`
(byte offset 6) matches the observed `instr_ptr` exactly, and offset 8 is where
execution should have resumed.

## Proposed fix

Replace the hand-rolled advance in `reifyFrameImpl` with the existing helper,
so the `EXTENDED_OPCODE` prefix and the inline-cache size are both accounted
for:

```cpp
frame->instr_ptr +=
    BytecodeInstruction{code_obj, cause_instr_idx}.nextInstrOffset().value();
```

**Not yet applied or verified** — this requires a CinderX rebuild. The two
error-handler branches below it (`is_instrumentation_deopt`,
`shouldResumeInterpreterInErrorHandler`) do the same
`inlineCacheSize(code_obj, cause_instr_idx)` lookup at the prefix index and
should be audited for the same mistake.

## Reproduction

```
python <emit erased deltablue at mask 68719476735 to a temp module>
CINDERX_JIT_LIST_FILE=min.list .venv/bin/python jitlist_run.py deltablue_erased.py   # rc 139
CINDERX_JIT_LIST_FILE=min.list CINDERX_JIT_ENABLE_HIR_INLINER=0 \
    .venv/bin/python jitlist_run.py deltablue_erased.py                              # clean
```

where `jitlist_run.py` sets `cinderx.jit.compile_after_n_calls(0)`, installs
the strict loader, imports the module as `__main__`, and calls `main()`.

The typed original needs no mask and no JIT list — plain
`compile_after_n_calls(0)` on `deltablue` advanced is enough to produce the
`StaticTypeError`.

No source-level minimization yet: a small standalone program with a
global-storing callee inlined into a caller that shadows the same name does
**not** reproduce, because nothing deopts there. The missing ingredient is a
deopt of the inlined frame; identifying which guard fires in `chain_test`
would give a compact test case.

## Unrelated latent issue found along the way

`_cinderx/cinderx/Jit/codegen/arch/aarch64.h:177` leaves **x18 and x17**
allocatable — `DISALLOWED_REGISTERS` excludes only x29, x30, xzr, x13, x14,
x16, d16, d17. Apple's ARM64 ABI reserves x18 for the platform, and x17 (IP1)
is clobbered by dyld stubs and linker veneers. A scratchpad probe confirms the
kernel zeroes x18 in an ordinary untraced process after roughly 290k loop
iterations.

This did **not** cause the DeltaBlue crash (the crash is inliner-gated and
survives with x18 zeroed all along), but any JIT value that ends up live in
x18 across a kernel upcall would be silently destroyed. Worth fixing
independently; not proven to bite today.
