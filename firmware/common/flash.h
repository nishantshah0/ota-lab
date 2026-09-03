#ifndef FLASH_H
#define FLASH_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/*
 * STM32F4 embedded flash programming through the FLASH controller
 * (RM0090 chapter 3). Erase granularity is one sector; programming is done
 * one 32-bit word at a time with PSIZE = x32 (needs VDD >= 2.7 V).
 *
 * The driver enforces the physical rule that programming can only clear
 * bits: writing a word whose set bits are not already set at the target
 * fails instead of silently producing garbage. Renode's flash model would
 * accept such a write, real silicon would not, so this keeps the two honest.
 */
bool flash_erase_sector(unsigned sector);
bool flash_program_word(uint32_t addr, uint32_t value);
bool flash_program(uint32_t addr, const void *data, size_t len); /* len % 4 == 0 */
bool flash_is_erased(uint32_t addr, size_t len);

#endif
