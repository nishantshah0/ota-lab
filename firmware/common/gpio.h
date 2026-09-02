#ifndef GPIO_H
#define GPIO_H

#include <stdint.h>
#include <stdbool.h>

enum gpio_mode {
    GPIO_MODE_INPUT  = 0,
    GPIO_MODE_OUTPUT = 1,
    GPIO_MODE_AF     = 2,
    GPIO_MODE_ANALOG = 3,
};

void gpio_set_mode(uint32_t port_base, unsigned pin, enum gpio_mode mode);
void gpio_set_af(uint32_t port_base, unsigned pin, unsigned af);
void gpio_write(uint32_t port_base, unsigned pin, bool high);
void gpio_toggle(uint32_t port_base, unsigned pin);

#endif
