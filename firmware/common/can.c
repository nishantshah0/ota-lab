#include "can.h"
#include "gpio.h"
#include "rcc.h"
#include "stm32f4_regs.h"

#define CAN CAN1_BASE
#define ACK_TIMEOUT 100000U

static can_rx_callback_t rx_cb;

static bool wait_msr(uint32_t mask, bool set)
{
    for (uint32_t i = 0; i < ACK_TIMEOUT; i++) {
        bool is_set = (CAN_MSR(CAN) & mask) != 0U;
        if (is_set == set) {
            return true;
        }
    }
    return false;
}

bool can_init(can_rx_callback_t rx_callback)
{
    rx_cb = rx_callback;

    rcc_enable_ahb1(RCC_AHB1ENR_GPIOBEN);
    rcc_enable_apb1(RCC_APB1ENR_CAN1EN);

    gpio_set_af(GPIOB_BASE, 8, 9); /* CAN1_RX */
    gpio_set_af(GPIOB_BASE, 9, 9); /* CAN1_TX */

    /*
     * bxCAN wakes up in sleep mode (SLAK set). Leave sleep, request
     * initialisation mode and wait for the hardware to acknowledge it.
     */
    CAN_MCR(CAN) &= ~CAN_MCR_SLEEP;
    CAN_MCR(CAN) |= CAN_MCR_INRQ;
    if (!wait_msr(CAN_MSR_INAK, true)) {
        return false;
    }

    /* Automatic bus-off recovery; keep automatic retransmission enabled. */
    CAN_MCR(CAN) |= CAN_MCR_ABOM;

    /*
     * Bit timing for 500 kbit/s from a 16 MHz APB1 clock:
     *   prescaler 2  -> 8 MHz time quanta
     *   1 (sync) + 13 (BS1) + 2 (BS2) = 16 tq per bit -> 500 kbit/s
     * Fields are stored minus one. SJW = 1 tq. Renode ignores timing.
     */
    CAN_BTR(CAN) = ((2U - 1U) << 0) | ((13U - 1U) << 16) | ((2U - 1U) << 20);

    /* Leave initialisation mode. */
    CAN_MCR(CAN) &= ~CAN_MCR_INRQ;
    if (!wait_msr(CAN_MSR_INAK, false)) {
        return false;
    }

    /*
     * Filter bank 0: single 32-bit identifier/mask pair with mask 0, so every
     * frame matches and lands in FIFO 0. Filter registers are writable only
     * while FINIT is set. Renode drops all frames until a bank is active.
     */
    CAN_FMR(CAN)   |= CAN_FMR_FINIT;
    CAN_FA1R(CAN)  &= ~1U;          /* deactivate bank 0 while configuring */
    CAN_FS1R(CAN)  |= 1U;           /* 32-bit scale */
    CAN_FM1R(CAN)  &= ~1U;          /* identifier mask mode */
    CAN_FFA1R(CAN) &= ~1U;          /* assign to FIFO 0 */
    CAN_F0R1(CAN)   = 0;            /* identifier */
    CAN_F0R2(CAN)   = 0;            /* mask: nothing is compared */
    CAN_FA1R(CAN)  |= 1U;           /* activate bank 0 */
    CAN_FMR(CAN)   &= ~CAN_FMR_FINIT;

    CAN_IER(CAN) |= CAN_IER_FMPIE0;  /* FIFO 0 message pending interrupt */
    nvic_enable_irq(IRQ_CAN1_RX0);
    return true;
}

bool can_send(const struct can_frame *frame)
{
    if ((CAN_TSR(CAN) & CAN_TSR_TME0) == 0U) {
        return false; /* mailbox 0 busy */
    }

    uint8_t dlc = frame->dlc > 8U ? 8U : frame->dlc;
    uint32_t lo = 0, hi = 0;
    for (unsigned i = 0; i < 4U; i++) {
        lo |= (uint32_t)frame->data[i] << (8U * i);
        hi |= (uint32_t)frame->data[i + 4U] << (8U * i);
    }

    /* Data registers first, identifier register with TXRQ last. */
    CAN_TDT0R(CAN) = dlc;
    CAN_TDL0R(CAN) = lo;
    CAN_TDH0R(CAN) = hi;

    uint32_t tir;
    if (frame->extended) {
        tir = ((frame->id & 0x1FFFFFFFU) << 3) | CAN_IR_IDE;
    } else {
        tir = (frame->id & 0x7FFU) << 21;
    }
    if (frame->remote) {
        tir |= CAN_IR_RTR;
    }
    CAN_TI0R(CAN) = tir | CAN_TIR_TXRQ;
    return true;
}

static void read_fifo0(struct can_frame *f)
{
    uint32_t rir  = CAN_RI0R(CAN);
    uint32_t rdtr = CAN_RDT0R(CAN);
    uint32_t lo   = CAN_RDL0R(CAN);
    uint32_t hi   = CAN_RDH0R(CAN);

    f->extended = (rir & CAN_IR_IDE) != 0U;
    f->remote   = (rir & CAN_IR_RTR) != 0U;
    f->id       = f->extended ? (rir >> 3) & 0x1FFFFFFFU : (rir >> 21) & 0x7FFU;
    f->dlc      = (uint8_t)(rdtr & 0xFU);
    for (unsigned i = 0; i < 4U; i++) {
        f->data[i]      = (uint8_t)(lo >> (8U * i));
        f->data[i + 4U] = (uint8_t)(hi >> (8U * i));
    }

    /* Release the output mailbox so the next frame moves to the front. */
    CAN_RF0R(CAN) |= CAN_RF0R_RFOM0;
}

void CAN1_RX0_IRQHandler(void)
{
    while ((CAN_RF0R(CAN) & CAN_RF0R_FMP0) != 0U) {
        struct can_frame f;
        read_fifo0(&f);
        if (rx_cb != 0) {
            rx_cb(&f);
        }
    }
}
