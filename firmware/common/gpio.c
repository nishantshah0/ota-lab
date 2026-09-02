#include "gpio.h"
#include "stm32f4_regs.h"

void gpio_set_mode(uint32_t port_base, unsigned pin, enum gpio_mode mode)
{
    uint32_t moder = GPIO_MODER(port_base);
    moder &= ~(3U << (pin * 2U));
    moder |= ((uint32_t)mode & 3U) << (pin * 2U);
    GPIO_MODER(port_base) = moder;
}

void gpio_set_af(uint32_t port_base, unsigned pin, unsigned af)
{
    /* AFRL covers pins 0..7, AFRH pins 8..15, four bits per pin. */
    if (pin < 8U) {
        uint32_t v = GPIO_AFRL(port_base);
        v &= ~(0xFU << (pin * 4U));
        v |= (af & 0xFU) << (pin * 4U);
        GPIO_AFRL(port_base) = v;
    } else {
        uint32_t v = GPIO_AFRH(port_base);
        v &= ~(0xFU << ((pin - 8U) * 4U));
        v |= (af & 0xFU) << ((pin - 8U) * 4U);
        GPIO_AFRH(port_base) = v;
    }
    gpio_set_mode(port_base, pin, GPIO_MODE_AF);
}

void gpio_write(uint32_t port_base, unsigned pin, bool high)
{
    /* BSRR: low half sets, high half resets, atomically. */
    GPIO_BSRR(port_base) = high ? (1U << pin) : (1U << (pin + 16U));
}

void gpio_toggle(uint32_t port_base, unsigned pin)
{
    GPIO_ODR(port_base) ^= (1U << pin);
}
