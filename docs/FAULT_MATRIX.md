# Fault matrix

Every fault the test suite injects, how it is injected, which test does it,
and what the firmware must do. All injection is external to the firmware:
Renode watchpoints and memory dumps, bit flips in flash images before a
boot, and knobs on the host-side sender. The production image carries no
test hooks; the `noconfirm` and `hang` application variants are separate
images built only for the rollback tests.

Mechanisms:

* **flash image**: the scenario is written into the flash file Renode loads
  (`tests/conftest.py`, `FlashBuilder`).
* **watchpoint**: `sysbus AddWatchpointHook <addr> 4 Write "cpu.IsHalted = True"`
  halts the core before that flash word is written; flash is dumped, Renode
  killed, and a fresh Renode boots from the dump (`RenodeLab.arm_flash_write_cut`).
* **sender**: `tools/ota_send.py` options `drop_rate`, `dup_rate`,
  `reorder_rate`, `corrupt_chunk`, `stop_after`.
* **monitor**: a frame injected through Renode's Python monitor.

| # | Fault | Mechanism | Test | Expected behaviour |
|---|-------|-----------|------|--------------------|
| 1 | Body CRC wrong in the running slot | flash image | `test_ab_boot.py::test_corrupt_crc_in_a_falls_back_to_b` | `slot A: BAD_CRC`, the other slot boots as FALLBACK and takes over on confirm |
| 2 | Signature wrong | flash image | `test_bad_signature_in_a_falls_back_to_b` | `BAD_SIGNATURE`, fallback to the other slot |
| 3 | Header targets the other slot | flash image | `test_other_invalid_images_are_rejected[wrong_slot]` | `WRONG_SLOT`, fallback |
| 4 | One body byte flipped after signing | flash image | `test_other_invalid_images_are_rejected[corrupt_body]` | `BAD_CRC`, fallback |
| 5 | Garbage in a slot | flash image | `test_other_invalid_images_are_rejected[garbage]` | `BAD_MAGIC`, fallback |
| 6 | Both slots invalid | flash image | `test_both_slots_bad_lands_in_safe_mode` | safe-mode image runs, feeds the watchdog, serves the log |
| 7 | New image never confirms | flash image (`noconfirm` variant) | `test_never_confirming_image_rolls_back_after_three_attempts` | three PENDING_TRIAL boots, then ROLLBACK to the active slot |
| 8 | New image hangs with interrupts off | flash image (`hang` variant) | `test_hanging_image_triggers_watchdog_and_counts_an_attempt` | watchdog reset, `RESET_WHILE_RUNNING` attributed to the slot, attempt counted |
| 9 | Pending slot fails validation | flash image | `test_pending_image_that_fails_validation_is_dropped` | pending flag cleared, active slot boots as FALLBACK |
| 10 | Torn journal record | flash image | `test_journal_ignores_torn_record` | previous record used, next record appended after the torn one |
| 11 | Stale records in both banks | flash image | `test_journal_prefers_highest_seq_across_banks` | highest sequence wins regardless of bank |
| 12 | Journal bank full | flash image | `test_journal_switches_bank_when_full` | other bank erased and used; current record never in the erased bank |
| 13 | Power cut mid journal record (bootloader attempt increment) | watchpoint | `test_power_cut_during_journal_write_recovers_last_record` | torn record ignored, previous state used |
| 14 | Corrupted chunk in transfer | sender `corrupt_chunk` | `test_ota_transfer.py::test_corrupted_chunk_is_caught_on_finish` | `BAD_CRC` at FINISH, slot never marked pending |
| 15 | Bad signature in transfer | flash image + sender | `test_bad_signature_is_rejected_on_finish` | `BAD_SIGNATURE` at FINISH |
| 16 | Lower version | sender | `test_lower_version_is_rejected_unless_forced` | `VERSION_LOW` at START, accepted with force |
| 17 | START lies about the version | custom START frames | `test_forged_version_in_start_does_not_bypass_anti_rollback` | refused at FINISH from the signed header |
| 18 | 5% frame loss | sender `drop_rate` | `test_transfer_survives_random_frame_loss` | NAK and retransmit, accepted |
| 19 | Reset mid transfer | console `reboot` | `test_reset_mid_transfer_resumes_from_last_good_chunk` | resume from the last progress record |
| 20 | Power cut inside a window write | watchpoint | `test_faults.py::test_power_cut_during_chunk_write_resumes[33,1000,3200]` | resume from the previous window boundary |
| 21 | Power cut on a progress record | watchpoint | `test_power_cut_during_progress_record_write` | torn record ignored, resume from the previous one |
| 22 | Power cut on the FINISH journal write | watchpoint | `test_power_cut_during_finish_journal_write` | boots A with nothing pending; next START resumes at the end and sends nothing |
| 23 | Power cut on the confirm record | watchpoint | `test_power_cut_during_confirm_write` | attempt record intact, trial 2 confirms, no rollback |
| 24 | Random cuts during an update | watchpoint, seeded | `test_random_power_cut_campaign[7,42]` | invariants hold after every restart, update completes |
| 25 | Bit rot in the new image body | flash dump + bit flips | `test_bit_rot_in_slot_body_is_caught_and_repaired` | `BAD_CRC`, pending dropped, active boots, re-transfer repairs |
| 26 | Bit rot in the signature | flash image + bit flip | `test_bit_rot_in_signature_is_caught` | `BAD_SIGNATURE`, fallback |
| 27 | Bit rot in the newest journal record | flash image + bit flip | `test_bit_rot_in_journal_record_falls_back_to_previous` | previous record used |
| 28 | Bit rot in a boot log entry | flash dump + bit flip | `test_bit_rot_in_boot_log_is_reported_not_fatal` | entry listed as TORN, boot unaffected |
| 29 | Duplicates, reordering and loss together | sender knobs | `test_bus_duplicates_reordering_and_loss` | accepted |
| 30 | Garbage frames and malformed control sequences | random frames | `test_garbage_and_malformed_frames_do_not_break_the_device` | device keeps its heartbeat and completes a normal transfer afterwards |
| 31 | One fleet device unreachable (safe mode) | flash image | `test_fleet.py::test_rollout_halts_on_corrupted_device` | rollout halts at that stage, earlier stages updated, later untouched |
| 32 | One fleet device never confirms | per-node image override | `test_rollout_halts_when_a_device_never_confirms` | that device reverts, rollout halts naming it |

Not covered, by choice or by limitation:

| Gap | Reason |
|-----|--------|
| DLC 0 CAN frames | Renode 1.16.1 crashes (see `renode_issue.md`); the case is skipped in `test_can.py` |
| Bit rot in the bootloader or safe-mode image | outside the recovery scope of the design; needs a programmer |
| Power cut during the bootloader's boot log write | the log is diagnostic; a torn entry is tolerated but not specifically tested |
| Flash erase duration versus the watchdog | Renode erases instantly; see the watchdog entry in `DESIGN_DECISIONS.md` |
