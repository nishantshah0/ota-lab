/*
 * Firmware delivery protocol over classic CAN (8 byte frames).
 * Mirrored by tools/ota_send.py; keep the two in sync.
 *
 * IDs (11-bit):
 *   0x710  host -> device control: START_A, START_B, FINISH, ABORT, STATUS
 *   0x711  host -> device data:    [seq lo][seq hi][6 payload bytes]
 *   0x712  device -> host replies: ACK, NAK, VERDICT, STATUS
 *
 * A transfer moves the whole signed image file (512 byte header + body) in
 * 6 byte chunks numbered from 0. The device collects a window of 32 chunks
 * (192 bytes) in RAM, programs it into the inactive slot, appends a
 * progress record to flash and only then sends ACK(next expected seq).
 * Out-of-order chunks trigger NAK(expected) and are dropped; the host
 * rewinds to the requested seq (go-back-N).
 *
 * Control frames, byte 0 is the type:
 *   START_A  01 | size u32 LE (header + body bytes) | ver major | minor | patch
 *   START_B  02 | flags | target slot | header crc32 LE | reserved
 *   FINISH   03
 *   ABORT    04
 *   STATUS   05
 *
 * Replies:
 *   ACK      20 | next seq u16 LE | code | reserved x4
 *              after START: code 0 = fresh transfer, 1 = resumed
 *              during data: window written, next expected seq
 *   NAK      21 | next seq u16 LE | code | reserved x4
 *              during data: code 1 = gap; after START: rejection code
 *   VERDICT  23 | code | detail u32 LE | reserved x2     (after FINISH)
 *   STATUS   24 | state | slot | next seq u16 LE | total u16 LE | reserved
 */
#ifndef OTA_PROTO_H
#define OTA_PROTO_H

#define OTA_ID_CTRL   0x710U
#define OTA_ID_DATA   0x711U
#define OTA_ID_REPLY  0x712U

#define OTA_CHUNK_BYTES   6U
#define OTA_WINDOW_CHUNKS 32U
#define OTA_WINDOW_BYTES  (OTA_CHUNK_BYTES * OTA_WINDOW_CHUNKS)

enum ota_ctrl_type {
    OTA_CTRL_START_A = 0x01,
    OTA_CTRL_START_B = 0x02,
    OTA_CTRL_FINISH  = 0x03,
    OTA_CTRL_ABORT   = 0x04,
    OTA_CTRL_STATUS  = 0x05,
};

enum ota_reply_type {
    OTA_REPLY_ACK     = 0x20,
    OTA_REPLY_NAK     = 0x21,
    OTA_REPLY_VERDICT = 0x23,
    OTA_REPLY_STATUS  = 0x24,
};

#define OTA_START_FLAG_FORCE 0x01U

/* NAK / VERDICT codes */
enum ota_code {
    OTA_OK               = 0,
    OTA_ERR_GAP          = 1,  /* NAK during data: seq out of order */
    OTA_ERR_NOT_STARTED  = 2,
    OTA_ERR_BAD_SIZE     = 3,
    OTA_ERR_SLOT_BUSY    = 4,  /* target slot is the running one */
    OTA_ERR_VERSION_LOW  = 5,  /* anti-rollback */
    OTA_ERR_FLASH        = 6,
    OTA_ERR_INCOMPLETE   = 7,  /* FINISH before all chunks arrived */
    OTA_ERR_BAD_MAGIC    = 8,
    OTA_ERR_BAD_HEADER   = 9,
    OTA_ERR_WRONG_SLOT   = 10,
    OTA_ERR_BAD_CRC      = 11,
    OTA_ERR_BAD_SIG      = 12,
    OTA_ERR_BAD_VECTORS  = 13,
    OTA_ERR_JOURNAL      = 14,
    OTA_ERR_TIMEOUT      = 15,
    OTA_ERR_SEQUENCE     = 16, /* START_B without START_A and the like */
};

enum ota_state {
    OTA_STATE_IDLE      = 0,
    OTA_STATE_RECEIVING = 1,
    OTA_STATE_DONE      = 2,
};

#endif
