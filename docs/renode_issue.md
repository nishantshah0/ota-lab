# Renode issue draft: CAN hub crashes the emulator on a zero-length frame from STMCAN

Status: draft, not filed. Reproduced on Renode 1.16.1 (build d66b0c2a-202602161036,
Windows portable .NET build) and on the Linux portable build in CI.

## Summary

When a `CAN.STMCAN` peripheral transmits a frame with DLC 0 into a
`CANHub`, the whole Renode process terminates with an unhandled
`NullReferenceException` in `CANMessageFrame.ToSocketCAN`, called from
`CANHub.Transmit` for the hub's traffic logging. The hub only catches
`RecoverableException`, so the null propagates through the CPU thread.

A `CANMessageFrame` constructed from Python with an empty byte array
encodes fine (`ToSocketCAN` returns 16 zero bytes), so the null `Data`
originates in `STMCAN`'s conversion of a DLC 0 mailbox to a
`CANMessageFrame`.

## Platform and script

Two machines on one hub; either one transmitting a DLC 0 frame is enough.
`platforms/boards/stm32f4_discovery.repl` is the stock file.

```
emulation CreateCANHub "canbus"

mach create "sender"
machine LoadPlatformDescription @platforms/boards/stm32f4_discovery.repl
sysbus LoadELF @sender.elf
connector Connect sysbus.can1 canbus

mach create "receiver"
machine LoadPlatformDescription @platforms/boards/stm32f4_discovery.repl
sysbus LoadELF @receiver.elf
connector Connect sysbus.can1 canbus

start
```

`receiver.elf` can be anything that initialises bxCAN with one active
filter bank (the crash is in the sender's transmit path, so the receiver
firmware does not matter).

## Sending firmware (bare metal, register level)

Initialisation as in RM0090: leave sleep, enter init mode, set BTR, leave
init mode, activate filter bank 0 as a 32-bit mask filter with mask 0.
Then transmit one frame with DLC 0:

```c
#define CAN1 0x40006400U
#define REG(a) (*(volatile uint32_t *)(a))

/* ... after init and filter setup ... */
while ((REG(CAN1 + 0x008) & (1U << 26)) == 0U) { }   /* TSR.TME0 */
REG(CAN1 + 0x184) = 0;                                /* TDT0R: DLC = 0 */
REG(CAN1 + 0x188) = 0;                                /* TDL0R */
REG(CAN1 + 0x18C) = 0;                                /* TDH0R */
REG(CAN1 + 0x180) = (0x123U << 21) | 1U;              /* TI0R: STID 0x123, TXRQ */
```

The same frame with DLC 1 or more is delivered normally.

## Observed

Console output, then the process exits with code 3762504530 (0xE0434352,
the .NET unhandled exception code):

```
Unhandled exception. System.NullReferenceException: Object reference not set to an instance of an object.
   at Antmicro.Renode.Core.CAN.CANMessageFrame.ToSocketCAN(Boolean useNetworkByteOrder)
   at Antmicro.Renode.Tools.Network.CANHub.Transmit(ICAN sender, CANMessageFrame message)
   at Antmicro.Renode.Tools.Network.CANHub.<>c__DisplayClass1_0.<AttachTo>b__0(CANMessageFrame message)
   at Antmicro.Renode.Peripherals.CAN.STMCAN.TransmitData(CANMessage msg)
   at Antmicro.Renode.Peripherals.CAN.STMCAN.WriteDoubleWord(Int64 address, UInt32 value)
   at Antmicro.Renode.Peripherals.Bus.SystemBus.WriteDoubleWord(UInt64 address, UInt32 value, IPeripheral context, Nullable`1 cpuState)
   at Antmicro.Renode.Peripherals.CPU.TranslationCPU.WriteDoubleWordToBus(UInt64 offset, UInt64 value, UInt64 cpuState)
   at WriteDoubleWordToBusWrapper(CortexMWrappers, UInt64, UInt64, UInt64)
--- End of stack trace from previous location ---
   at Antmicro.Renode.Utilities.Binding.ExceptionKeeper.ThrowExceptions()
   at TlibExecute(CortexMWrappers, Int32)
   at Antmicro.Renode.Peripherals.CPU.TranslationCPU.ExecuteInstructions(UInt64 numberOfInstructionsToExecute, UInt64& numberOfExecutedInstructions)
   at Antmicro.Renode.Peripherals.CPU.BaseCPU.CpuThreadBodyInner(Boolean singleStep, Boolean skipThisRound)
   at Antmicro.Renode.Peripherals.CPU.BaseCPU.CpuThreadBody()
```

## Expected

A DLC 0 frame is valid classic CAN. It should be delivered to the other
hub members with an empty payload, and a failure inside the hub's logging
should never take the emulator down.

## Monitor check that isolates the null to STMCAN

With the emulation running, in the monitor:

```
python "from Antmicro.Renode.Core.CAN import CANMessageFrame"
python "from System import Array, Byte"
python "print(list(CANMessageFrame(0, Array[Byte]([])).ToSocketCAN(True)))"
```

prints sixteen zero bytes, so `ToSocketCAN` handles an empty (non-null)
array. Injecting the same empty frame into a receiver with
`OnFrameReceived` also works; the receiver's echo of it is what crashes,
because the frame then passes through `STMCAN.TransmitData`.

## Workaround used in this project

Firmware on this bus never transmits DLC 0 frames; the test for that case
is skipped with the reason above, and fuzzers only generate 1 to 8 byte
payloads (see `tests/test_can.py` and `tests/test_faults.py`).
