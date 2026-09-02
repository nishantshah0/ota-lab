/*
 * Minimal Cortex-M4 startup for STM32F407: vector table, reset handler,
 * default exception and interrupt handlers.
 */
#include <stdint.h>

/* Symbols provided by the linker script (see stm32f4.ld). */
extern uint32_t _estack;
extern uint32_t _sidata, _sdata, _edata;
extern uint32_t _sbss, _ebss;

int main(void);

void Reset_Handler(void);
void Default_Handler(void);

/* Core exceptions. */
void NMI_Handler(void)        __attribute__((weak, alias("Default_Handler")));
void HardFault_Handler(void)  __attribute__((weak, alias("Default_Handler")));
void MemManage_Handler(void)  __attribute__((weak, alias("Default_Handler")));
void BusFault_Handler(void)   __attribute__((weak, alias("Default_Handler")));
void UsageFault_Handler(void) __attribute__((weak, alias("Default_Handler")));
void SVC_Handler(void)        __attribute__((weak, alias("Default_Handler")));
void DebugMon_Handler(void)   __attribute__((weak, alias("Default_Handler")));
void PendSV_Handler(void)     __attribute__((weak, alias("Default_Handler")));
void SysTick_Handler(void)    __attribute__((weak, alias("Default_Handler")));

/* STM32F407 external interrupts, in vector order (position 0 = WWDG). */
#define WEAK_IRQ(name) void name(void) __attribute__((weak, alias("Default_Handler")))
WEAK_IRQ(WWDG_IRQHandler);               /* 0  */
WEAK_IRQ(PVD_IRQHandler);                /* 1  */
WEAK_IRQ(TAMP_STAMP_IRQHandler);         /* 2  */
WEAK_IRQ(RTC_WKUP_IRQHandler);           /* 3  */
WEAK_IRQ(FLASH_IRQHandler);              /* 4  */
WEAK_IRQ(RCC_IRQHandler);                /* 5  */
WEAK_IRQ(EXTI0_IRQHandler);              /* 6  */
WEAK_IRQ(EXTI1_IRQHandler);              /* 7  */
WEAK_IRQ(EXTI2_IRQHandler);              /* 8  */
WEAK_IRQ(EXTI3_IRQHandler);              /* 9  */
WEAK_IRQ(EXTI4_IRQHandler);              /* 10 */
WEAK_IRQ(DMA1_Stream0_IRQHandler);       /* 11 */
WEAK_IRQ(DMA1_Stream1_IRQHandler);       /* 12 */
WEAK_IRQ(DMA1_Stream2_IRQHandler);       /* 13 */
WEAK_IRQ(DMA1_Stream3_IRQHandler);       /* 14 */
WEAK_IRQ(DMA1_Stream4_IRQHandler);       /* 15 */
WEAK_IRQ(DMA1_Stream5_IRQHandler);       /* 16 */
WEAK_IRQ(DMA1_Stream6_IRQHandler);       /* 17 */
WEAK_IRQ(ADC_IRQHandler);                /* 18 */
WEAK_IRQ(CAN1_TX_IRQHandler);            /* 19 */
WEAK_IRQ(CAN1_RX0_IRQHandler);           /* 20 */
WEAK_IRQ(CAN1_RX1_IRQHandler);           /* 21 */
WEAK_IRQ(CAN1_SCE_IRQHandler);           /* 22 */
WEAK_IRQ(EXTI9_5_IRQHandler);            /* 23 */
WEAK_IRQ(TIM1_BRK_TIM9_IRQHandler);      /* 24 */
WEAK_IRQ(TIM1_UP_TIM10_IRQHandler);      /* 25 */
WEAK_IRQ(TIM1_TRG_COM_TIM11_IRQHandler); /* 26 */
WEAK_IRQ(TIM1_CC_IRQHandler);            /* 27 */
WEAK_IRQ(TIM2_IRQHandler);               /* 28 */
WEAK_IRQ(TIM3_IRQHandler);               /* 29 */
WEAK_IRQ(TIM4_IRQHandler);               /* 30 */
WEAK_IRQ(I2C1_EV_IRQHandler);            /* 31 */
WEAK_IRQ(I2C1_ER_IRQHandler);            /* 32 */
WEAK_IRQ(I2C2_EV_IRQHandler);            /* 33 */
WEAK_IRQ(I2C2_ER_IRQHandler);            /* 34 */
WEAK_IRQ(SPI1_IRQHandler);               /* 35 */
WEAK_IRQ(SPI2_IRQHandler);               /* 36 */
WEAK_IRQ(USART1_IRQHandler);             /* 37 */
WEAK_IRQ(USART2_IRQHandler);             /* 38 */
WEAK_IRQ(USART3_IRQHandler);             /* 39 */
WEAK_IRQ(EXTI15_10_IRQHandler);          /* 40 */
WEAK_IRQ(RTC_Alarm_IRQHandler);          /* 41 */
WEAK_IRQ(OTG_FS_WKUP_IRQHandler);        /* 42 */
WEAK_IRQ(TIM8_BRK_TIM12_IRQHandler);     /* 43 */
WEAK_IRQ(TIM8_UP_TIM13_IRQHandler);      /* 44 */
WEAK_IRQ(TIM8_TRG_COM_TIM14_IRQHandler); /* 45 */
WEAK_IRQ(TIM8_CC_IRQHandler);            /* 46 */
WEAK_IRQ(DMA1_Stream7_IRQHandler);       /* 47 */
WEAK_IRQ(FSMC_IRQHandler);               /* 48 */
WEAK_IRQ(SDIO_IRQHandler);               /* 49 */
WEAK_IRQ(TIM5_IRQHandler);               /* 50 */
WEAK_IRQ(SPI3_IRQHandler);               /* 51 */
WEAK_IRQ(UART4_IRQHandler);              /* 52 */
WEAK_IRQ(UART5_IRQHandler);              /* 53 */
WEAK_IRQ(TIM6_DAC_IRQHandler);           /* 54 */
WEAK_IRQ(TIM7_IRQHandler);               /* 55 */
WEAK_IRQ(DMA2_Stream0_IRQHandler);       /* 56 */
WEAK_IRQ(DMA2_Stream1_IRQHandler);       /* 57 */
WEAK_IRQ(DMA2_Stream2_IRQHandler);       /* 58 */
WEAK_IRQ(DMA2_Stream3_IRQHandler);       /* 59 */
WEAK_IRQ(DMA2_Stream4_IRQHandler);       /* 60 */
WEAK_IRQ(ETH_IRQHandler);                /* 61 */
WEAK_IRQ(ETH_WKUP_IRQHandler);           /* 62 */
WEAK_IRQ(CAN2_TX_IRQHandler);            /* 63 */
WEAK_IRQ(CAN2_RX0_IRQHandler);           /* 64 */
WEAK_IRQ(CAN2_RX1_IRQHandler);           /* 65 */
WEAK_IRQ(CAN2_SCE_IRQHandler);           /* 66 */
WEAK_IRQ(OTG_FS_IRQHandler);             /* 67 */
WEAK_IRQ(DMA2_Stream5_IRQHandler);       /* 68 */
WEAK_IRQ(DMA2_Stream6_IRQHandler);       /* 69 */
WEAK_IRQ(DMA2_Stream7_IRQHandler);       /* 70 */
WEAK_IRQ(USART6_IRQHandler);             /* 71 */
WEAK_IRQ(I2C3_EV_IRQHandler);            /* 72 */
WEAK_IRQ(I2C3_ER_IRQHandler);            /* 73 */
WEAK_IRQ(OTG_HS_EP1_OUT_IRQHandler);     /* 74 */
WEAK_IRQ(OTG_HS_EP1_IN_IRQHandler);      /* 75 */
WEAK_IRQ(OTG_HS_WKUP_IRQHandler);        /* 76 */
WEAK_IRQ(OTG_HS_IRQHandler);             /* 77 */
WEAK_IRQ(DCMI_IRQHandler);               /* 78 */
WEAK_IRQ(CRYP_IRQHandler);               /* 79 */
WEAK_IRQ(HASH_RNG_IRQHandler);           /* 80 */
WEAK_IRQ(FPU_IRQHandler);                /* 81 */

typedef void (*vector_t)(void);

/*
 * The vector table. Entry 0 is the initial stack pointer, entry 1 the reset
 * vector, entries 2..15 the core exceptions, and entry 16 onward the NVIC
 * external interrupts. Total 16 + 82 = 98 entries = 392 bytes.
 */
__attribute__((section(".isr_vector"), used))
const vector_t g_vector_table[16 + 82] = {
    (vector_t)&_estack,
    Reset_Handler,
    NMI_Handler,
    HardFault_Handler,
    MemManage_Handler,
    BusFault_Handler,
    UsageFault_Handler,
    0, 0, 0, 0,
    SVC_Handler,
    DebugMon_Handler,
    0,
    PendSV_Handler,
    SysTick_Handler,

    WWDG_IRQHandler,
    PVD_IRQHandler,
    TAMP_STAMP_IRQHandler,
    RTC_WKUP_IRQHandler,
    FLASH_IRQHandler,
    RCC_IRQHandler,
    EXTI0_IRQHandler,
    EXTI1_IRQHandler,
    EXTI2_IRQHandler,
    EXTI3_IRQHandler,
    EXTI4_IRQHandler,
    DMA1_Stream0_IRQHandler,
    DMA1_Stream1_IRQHandler,
    DMA1_Stream2_IRQHandler,
    DMA1_Stream3_IRQHandler,
    DMA1_Stream4_IRQHandler,
    DMA1_Stream5_IRQHandler,
    DMA1_Stream6_IRQHandler,
    ADC_IRQHandler,
    CAN1_TX_IRQHandler,
    CAN1_RX0_IRQHandler,
    CAN1_RX1_IRQHandler,
    CAN1_SCE_IRQHandler,
    EXTI9_5_IRQHandler,
    TIM1_BRK_TIM9_IRQHandler,
    TIM1_UP_TIM10_IRQHandler,
    TIM1_TRG_COM_TIM11_IRQHandler,
    TIM1_CC_IRQHandler,
    TIM2_IRQHandler,
    TIM3_IRQHandler,
    TIM4_IRQHandler,
    I2C1_EV_IRQHandler,
    I2C1_ER_IRQHandler,
    I2C2_EV_IRQHandler,
    I2C2_ER_IRQHandler,
    SPI1_IRQHandler,
    SPI2_IRQHandler,
    USART1_IRQHandler,
    USART2_IRQHandler,
    USART3_IRQHandler,
    EXTI15_10_IRQHandler,
    RTC_Alarm_IRQHandler,
    OTG_FS_WKUP_IRQHandler,
    TIM8_BRK_TIM12_IRQHandler,
    TIM8_UP_TIM13_IRQHandler,
    TIM8_TRG_COM_TIM14_IRQHandler,
    TIM8_CC_IRQHandler,
    DMA1_Stream7_IRQHandler,
    FSMC_IRQHandler,
    SDIO_IRQHandler,
    TIM5_IRQHandler,
    SPI3_IRQHandler,
    UART4_IRQHandler,
    UART5_IRQHandler,
    TIM6_DAC_IRQHandler,
    TIM7_IRQHandler,
    DMA2_Stream0_IRQHandler,
    DMA2_Stream1_IRQHandler,
    DMA2_Stream2_IRQHandler,
    DMA2_Stream3_IRQHandler,
    DMA2_Stream4_IRQHandler,
    ETH_IRQHandler,
    ETH_WKUP_IRQHandler,
    CAN2_TX_IRQHandler,
    CAN2_RX0_IRQHandler,
    CAN2_RX1_IRQHandler,
    CAN2_SCE_IRQHandler,
    OTG_FS_IRQHandler,
    DMA2_Stream5_IRQHandler,
    DMA2_Stream6_IRQHandler,
    DMA2_Stream7_IRQHandler,
    USART6_IRQHandler,
    I2C3_EV_IRQHandler,
    I2C3_ER_IRQHandler,
    OTG_HS_EP1_OUT_IRQHandler,
    OTG_HS_EP1_IN_IRQHandler,
    OTG_HS_WKUP_IRQHandler,
    OTG_HS_IRQHandler,
    DCMI_IRQHandler,
    CRYP_IRQHandler,
    HASH_RNG_IRQHandler,
    FPU_IRQHandler,
};

/*
 * Image descriptor, placed right after the vector table by the linker script.
 * A host tool can locate it at a fixed offset (392 bytes) from the image start.
 */
struct fw_info {
    uint32_t magic;
    char     version[16];
    uint32_t reserved[3];
};

__attribute__((section(".fw_info"), used))
const struct fw_info g_fw_info = {
    .magic    = 0x4F54414CU, /* "OTAL" */
    .version  = FW_VERSION,
    .reserved = {0, 0, 0},
};

#define SCB_VTOR (*(volatile uint32_t *)0xE000ED08U)

void Reset_Handler(void)
{
    /*
     * Point the core at our vector table. Redundant when we run from the
     * start of flash (the reset default), but required once a bootloader
     * jumps to an application linked at a slot address.
     */
    SCB_VTOR = (uint32_t)g_vector_table;

    /* Copy .data from its load address in flash to its run address in RAM. */
    uint32_t *src = &_sidata;
    uint32_t *dst = &_sdata;
    while (dst < &_edata) {
        *dst++ = *src++;
    }

    /* Zero .bss. */
    dst = &_sbss;
    while (dst < &_ebss) {
        *dst++ = 0;
    }

    main();

    /* main() must never return on a bare-metal target; park the CPU. */
    for (;;) {
        __asm volatile ("wfi");
    }
}

void Default_Handler(void)
{
    /* An unexpected exception or interrupt. Spin so a debugger can inspect. */
    for (;;) {
    }
}
