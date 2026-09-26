# ==========================
# ESP32 --> Orin (0x01 - 0x1F)
# ==========================
uint8 ESP_ACT_SPEED         = 0x01
uint8 ESP_ACT_ACCELERATION  = 0x02
uint8 ESP_ACT_BRAKING       = 0x03
uint8 ESP_ACT_STEERING      = 0x04
uint8 ESP_MISION            = 0x05
uint8 ESP_MACHINE_STATE     = 0x06
uint8 ESP_ACT_SHUTDOWN      = 0x07
uint8 ESP_HEARTBEAT         = 0x08
uint8 ESP_COMPLETE          = 0x09
uint8 ESP_DIAG_STEERING     = 0x0A
uint8 ESP_HEALTH_STATUS     = 0x0B
uint8 ESP_PNEUMATIC         = 0x0C
uint8 ESP_STEER_PID         = 0x0D
uint8 ESP_PEDALS            = 0x0E
uint8 ESP_SAFETY_STATUS     = 0x0F


# ==========================
# Orin --> ESP32 (0x20 - 0x3F)
# ==========================
uint8 ORIN_TARG_THROTTLE    = 0x20
uint8 ORIN_TARG_BRAKING     = 0x21
uint8 ORIN_TARG_STEERING    = 0x22
uint8 ORIN_MISION           = 0x23
uint8 ORIN_MACHINE_STATE    = 0x24
uint8 ORIN_HEARTBEAT        = 0x25
uint8 ORIN_SHUTDOWN         = 0x26
uint8 ORIN_COMPLETE         = 0x27
uint8 ORIN_CALIBRATE_STEERING = 0x28
uint8 ORIN_STEER_MODE       = 0x29
uint8 ORIN_COMPRESSOR_DISABLE = 0x2A
uint8 ORIN_STEER_PID        = 0x2B
uint8 ORIN_SAFETY_RESET     = 0x2C

# ==========================
# Others (0x40 - 0xFF)
# ==========================

uint8 type        # Message type
int32[] payload   # Variable length payload
