# U-NNN result

<!-- Written by the agent that built the unit, to results/<unit-id>.md.

     This is the build's ground truth. Unit status, gate status and the
     provenance pins are folded out of these files on demand by explain.py;
     there is no separate status file to keep in sync, and nothing here is
     ever rewritten. Append, never edit.

     The headings below are load-bearing: `## Verification` is where evidence
     is looked for, and a unit counts as built only when that section carries
     the real output. A result that says "done" with nothing under Verification
     is reported as *claimed*, not *built*. -->

**Unit:** U-NNN
**Status:** verified | blocked | in-progress
**Constitution:** C1 `<hash from the packet header, echoed back>`
**Base commit:** `<the commit pinned in the packet>`
**Packet hash:** `<from the packet header>`
**Verified by:** `<the exact command from the unit contract>`

## Verification

<!-- The actual, unedited output of the command above. Not a summary of it, not
     "all tests passed" -- the output. This section is the difference between a
     unit that is built and a unit that merely says so. -->

```
<paste the real output here>
```

## Files

<!-- Created or modified. Every path must fall inside the unit's `Owns` set;
     if one does not, that belongs under Blockages instead, not here. -->

-

## Missing context

<!-- What you needed and were not given, and what you did instead. A
     first-class signal, not a complaint: repeated reports across units mean
     the concept model is wrong and will be revised, so silence here is worse
     than an awkward entry. Write "(none)" if there was none. -->

(none)

## Blockages

<!-- What you expected, what you found, what you need. Stopping and reporting
     is correct behaviour and is never counted against the unit. Improvising
     past a missing asset is the failure this build is designed against.
     Write "(none)" if there were none. -->

(none)
