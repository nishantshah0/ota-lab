# Bootloader: sectors 0..1, 32 KiB. Optimised for size because Ed25519 and
# SHA-512 are the bulk of it.
add_firmware(boot 0x08000000 0x8000 main.c)
target_compile_options(boot.elf PRIVATE -Os)
