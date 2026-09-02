/*
 * Linker script for STM32F407VG (Cortex-M4), bare metal.
 *
 * Physical memory map of the part:
 *
 *   0x0800_0000 .. 0x080F_FFFF   1 MiB main flash (12 sectors, see below)
 *   0x1FFF_0000 .. 0x1FFF_77FF   system memory (ST boot ROM, read only)
 *   0x1FFF_C000 .. 0x1FFF_C00F   option bytes
 *   0x2000_0000 .. 0x2001_FFFF   128 KiB SRAM (SRAM1 112K + SRAM2 16K, contiguous)
 *   0x1000_0000 .. 0x1000_FFFF   64 KiB CCM RAM (core coupled, CPU only, no DMA)
 *
 * Flash sector layout (matters for the bootloader phase, erase is per sector):
 *
 *   sector  0   0x0800_0000   16 KiB
 *   sector  1   0x0800_4000   16 KiB
 *   sector  2   0x0800_8000   16 KiB
 *   sector  3   0x0800_C000   16 KiB
 *   sector  4   0x0801_0000   64 KiB
 *   sector  5   0x0802_0000  128 KiB
 *   sector  6   0x0804_0000  128 KiB
 *   sector  7   0x0806_0000  128 KiB
 *   sector  8   0x0808_0000  128 KiB
 *   sector  9   0x080A_0000  128 KiB
 *   sector 10   0x080C_0000  128 KiB
 *   sector 11   0x080E_0000  128 KiB
 *
 * Planned partitioning for the A/B bootloader (not enforced yet in phase 1):
 *
 *   bootloader   0x0800_0000  sectors 0..3   64 KiB
 *   boot state   0x0801_0000  sector 4       64 KiB   (slot flags, trial counters)
 *   slot A       0x0802_0000  sector 5      128 KiB
 *   slot B       0x0804_0000  sector 6      128 KiB
 *   spare        0x0806_0000  sectors 7..11
 *
 * Phase 1 links the application at the start of flash (no bootloader yet).
 * The origin and length can be overridden from the command line with
 *   -Wl,--defsym=__flash_origin=0x08020000 -Wl,--defsym=__flash_length=0x20000
 * which is exactly what the bootloader phase will do to place the app in a slot.
 * The Cortex-M4 requires the vector table to be aligned to at least 512 bytes
 * (next power of two above 98 entries * 4 bytes), so slot origins must respect
 * that. Sector boundaries satisfy this trivially.
 */

ENTRY(Reset_Handler)

__flash_origin = DEFINED(__flash_origin) ? __flash_origin : 0x08000000;
__flash_length = DEFINED(__flash_length) ? __flash_length : 1024K;

MEMORY
{
    FLASH (rx)  : ORIGIN = __flash_origin, LENGTH = __flash_length
    RAM   (rwx) : ORIGIN = 0x20000000,     LENGTH = 128K
    CCM   (rw)  : ORIGIN = 0x10000000,     LENGTH = 64K
}

/* Top of RAM. The first word of the vector table is the initial stack pointer,
 * and the stack grows downward from here. */
_estack = ORIGIN(RAM) + LENGTH(RAM);

_min_stack_size = 0x800;   /* 2 KiB, reserved at link time below */
_min_heap_size  = 0x0;     /* no heap in phase 1 */

SECTIONS
{
    /*
     * The vector table must be the first thing in flash: the core fetches
     * SP from offset 0 and PC from offset 4 at reset. KEEP prevents
     * --gc-sections from dropping it because nothing references it.
     * The bootloader later reads this same table from a slot to find the
     * application entry point and stack.
     */
    .isr_vector :
    {
        . = ALIGN(4);
        KEEP(*(.isr_vector))
        . = ALIGN(4);
    } > FLASH

    /*
     * Image descriptor placed immediately after the vector table at a fixed
     * offset. Phase 1 stores only a magic and a version string. The signed
     * header of later phases will replace this with a full descriptor
     * (length, hash, signature, slot flags) at the same fixed location.
     */
    .fw_info :
    {
        . = ALIGN(4);
        KEEP(*(.fw_info))
        . = ALIGN(4);
    } > FLASH

    /* Code and read-only data live in flash and execute in place. */
    .text :
    {
        . = ALIGN(4);
        *(.text)
        *(.text*)
        *(.glue_7)         /* ARM/Thumb interworking veneers */
        *(.glue_7t)
        *(.eh_frame)
        KEEP(*(.init))
        KEEP(*(.fini))
        . = ALIGN(4);
        _etext = .;
    } > FLASH

    .rodata :
    {
        . = ALIGN(4);
        *(.rodata)
        *(.rodata*)
        . = ALIGN(4);
    } > FLASH

    /* Exception unwinding tables. Unused here but harmless, and GCC emits them. */
    .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } > FLASH
    .ARM.exidx :
    {
        __exidx_start = .;
        *(.ARM.exidx*)
        __exidx_end = .;
    } > FLASH

    /*
     * Initialised data. It is stored in flash (load address, LMA) right after
     * the code, but the program uses it in RAM (virtual address, VMA).
     * Reset_Handler copies [_sidata, _sidata + (_edata - _sdata)) to _sdata.
     * This copy is why the image size in flash is larger than .text alone.
     */
    _sidata = LOADADDR(.data);
    .data :
    {
        . = ALIGN(4);
        _sdata = .;
        *(.data)
        *(.data*)
        . = ALIGN(4);
        _edata = .;
    } > RAM AT > FLASH

    /* End of the flash image. Later phases hash and sign [__flash_origin, _eimage). */
    _eimage = LOADADDR(.data) + SIZEOF(.data);

    /* Zero initialised data. Occupies RAM only; Reset_Handler zeroes it. */
    .bss (NOLOAD) :
    {
        . = ALIGN(4);
        _sbss = .;
        __bss_start__ = _sbss;
        *(.bss)
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        _ebss = .;
        __bss_end__ = _ebss;
    } > RAM

    /*
     * Reserve the minimum stack (and heap, if any) so that the linker fails
     * loudly if static data grows into the space the stack needs.
     */
    ._user_heap_stack (NOLOAD) :
    {
        . = ALIGN(8);
        _heap_start = .;
        . = . + _min_heap_size;
        _heap_end = .;
        . = . + _min_stack_size;
        . = ALIGN(8);
    } > RAM

    /*
     * Data the bootloader and application will share across a reset
     * (boot reason, requested slot, update status). NOLOAD means neither
     * startup nor the loader touches it. Unused in phase 1, but reserving it
     * now keeps the layout stable.
     */
    .noinit (NOLOAD) :
    {
        . = ALIGN(4);
        _snoinit = .;
        KEEP(*(.noinit))
        KEEP(*(.noinit*))
        . = ALIGN(4);
        _enoinit = .;
    } > CCM

    /DISCARD/ :
    {
        libc.a ( * )
        libm.a ( * )
        *(.ARM.attributes)
        *(.comment)
    }
}
