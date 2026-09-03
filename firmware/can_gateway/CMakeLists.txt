# Gateway runs on its own machine straight from the start of flash, no
# bootloader and no header.
add_firmware(can_gateway 0x08000000 0x100000 main.c)
