# Safe-mode image: first 16 KiB of sector 4, no header, trusted by virtue
# of being installed with the bootloader.
add_firmware(safe 0x08010000 0x4000 main.c)
