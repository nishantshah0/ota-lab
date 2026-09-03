cmake_minimum_required(VERSION 3.18)

# Select the cross toolchain unless the caller already did.
if(NOT CMAKE_TOOLCHAIN_FILE)
    set(CMAKE_TOOLCHAIN_FILE ${CMAKE_CURRENT_SOURCE_DIR}/cmake/arm-none-eabi.cmake)
endif()

project(ota_lab C ASM)

set(FW_VERSION "0.2.0")

# Timer clock seen by TIM2. Renode models STM32 timers at a fixed 10 MHz
# (see platforms/cpus/stm32f4.repl). On real silicon with HSI and no APB
# prescaler it would be 16 MHz. Override with -DTIMER_CLOCK_HZ=... if needed.
set(TIMER_CLOCK_HZ 10000000 CACHE STRING "Clock feeding TIM2 in Hz")

# Signing key for the images built here. keys/dev_ed25519.key is a
# development key committed for the emulator lab; a real product keeps it
# in an HSM and only the public half in firmware/boot/public_key.c.
set(SIGNING_KEY ${CMAKE_CURRENT_SOURCE_DIR}/keys/dev_ed25519.key CACHE FILEPATH "Ed25519 private key (hex seed)")

find_package(Python3 3.9 COMPONENTS Interpreter REQUIRED)

set(COMMON_DIR ${CMAKE_CURRENT_SOURCE_DIR}/firmware/common)
set(LINKER_SCRIPT ${COMMON_DIR}/stm32f4.ld)
set(TOOLS_DIR ${CMAKE_CURRENT_SOURCE_DIR}/tools)

add_compile_options(
    -mcpu=cortex-m4 -mthumb -mfloat-abi=soft
    -std=c11 -ffreestanding -fno-builtin
    -ffunction-sections -fdata-sections
    -Wall -Wextra -Werror -Wno-unused-parameter
    -O2 -g3
)
add_compile_definitions(
    FW_VERSION="${FW_VERSION}"
    TIMER_CLOCK_HZ=${TIMER_CLOCK_HZ}
)
add_link_options(
    -mcpu=cortex-m4 -mthumb -mfloat-abi=soft
    -nostdlib -nostartfiles
    -Wl,--gc-sections
    -T${LINKER_SCRIPT}
)

add_library(common STATIC
    ${COMMON_DIR}/startup.c
    ${COMMON_DIR}/libc_min.c
    ${COMMON_DIR}/rcc.c
    ${COMMON_DIR}/gpio.c
    ${COMMON_DIR}/uart.c
    ${COMMON_DIR}/timer.c
    ${COMMON_DIR}/can.c
    ${COMMON_DIR}/fmt.c
    ${COMMON_DIR}/crc32.c
    ${COMMON_DIR}/flash.c
    ${COMMON_DIR}/journal.c
    ${COMMON_DIR}/bootlog.c
    ${COMMON_DIR}/image.c
    ${COMMON_DIR}/iwdg.c
    ${COMMON_DIR}/ota.c
    ${COMMON_DIR}/console.c
)
target_include_directories(common PUBLIC ${COMMON_DIR})

# Monocypher 4.0.2, vendored. Only the Ed25519 (SHA-512) path is linked in;
# --gc-sections drops the rest.
add_library(monocypher STATIC
    ${COMMON_DIR}/monocypher/monocypher.c
    ${COMMON_DIR}/monocypher/monocypher-ed25519.c
)
target_include_directories(monocypher PUBLIC ${COMMON_DIR}/monocypher)
target_compile_options(monocypher PRIVATE -Os)

# add_firmware(<name> <flash origin> <flash length> <sources...>)
# Links an ELF at the given flash window, produces .bin and .map next to it
# and prints the section sizes.
function(add_firmware name origin length)
    add_executable(${name}.elf ${ARGN})
    target_link_libraries(${name}.elf PRIVATE common gcc)
    target_link_options(${name}.elf PRIVATE
        -Wl,--defsym=__flash_origin=${origin}
        -Wl,--defsym=__flash_length=${length}
        -Wl,-Map=${CMAKE_CURRENT_BINARY_DIR}/${name}.map)
    set_target_properties(${name}.elf PROPERTIES LINK_DEPENDS ${LINKER_SCRIPT})
    add_custom_command(TARGET ${name}.elf POST_BUILD
        COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:${name}.elf> ${CMAKE_CURRENT_BINARY_DIR}/${name}.bin
        COMMAND ${CMAKE_SIZE} $<TARGET_FILE:${name}.elf>
        COMMENT "Producing ${name}.bin and size report")
endfunction()

# add_signed_image(<name> <A|B>): wraps <name>.bin in a signed header.
function(add_signed_image name slot)
    set(bin ${CMAKE_CURRENT_BINARY_DIR}/${name}.bin)
    set(out ${CMAKE_CURRENT_BINARY_DIR}/${name}.signed.bin)
    add_custom_command(OUTPUT ${out}
        COMMAND Python3::Interpreter ${TOOLS_DIR}/sign_image.py
                --key ${SIGNING_KEY} --slot ${slot} --version ${FW_VERSION}
                --in ${bin} --out ${out}
        DEPENDS ${name}.elf ${TOOLS_DIR}/sign_image.py ${TOOLS_DIR}/otaimg.py ${SIGNING_KEY}
        COMMENT "Signing ${name} for slot ${slot}")
    add_custom_target(${name}_signed ALL DEPENDS ${out})
endfunction()

add_subdirectory(firmware/boot)
add_subdirectory(firmware/safe)
add_subdirectory(firmware/app)
add_subdirectory(firmware/can_gateway)

# Default flash image: bootloader, safe mode, good app in slot A, empty B,
# erased journal and log. This is what "renode renode/ota_lab.resc" boots.
set(FLASH_DIR ${CMAKE_BINARY_DIR}/flash)
add_custom_command(OUTPUT ${FLASH_DIR}/default.bin
    COMMAND ${CMAKE_COMMAND} -E make_directory ${FLASH_DIR}
    COMMAND Python3::Interpreter ${TOOLS_DIR}/mkflash.py
            --boot ${CMAKE_BINARY_DIR}/firmware/boot/boot.bin
            --safe ${CMAKE_BINARY_DIR}/firmware/safe/safe.bin
            --slot-a ${CMAKE_BINARY_DIR}/firmware/app/app_good_A.signed.bin
            --out ${FLASH_DIR}/default.bin
    DEPENDS boot.elf safe.elf app_good_A_signed ${TOOLS_DIR}/mkflash.py ${TOOLS_DIR}/flashimage.py
    COMMENT "Assembling default flash image")
add_custom_target(default_flash ALL DEPENDS ${FLASH_DIR}/default.bin)
