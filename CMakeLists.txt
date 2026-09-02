cmake_minimum_required(VERSION 3.18)

# Select the cross toolchain unless the caller already did.
if(NOT CMAKE_TOOLCHAIN_FILE)
    set(CMAKE_TOOLCHAIN_FILE ${CMAKE_CURRENT_SOURCE_DIR}/cmake/arm-none-eabi.cmake)
endif()

project(ota_lab C ASM)

set(FW_VERSION "0.1.0")

# Timer clock seen by TIM2. Renode models STM32 timers at a fixed 10 MHz
# (see platforms/cpus/stm32f4.repl). On real silicon with HSI and no APB
# prescaler it would be 16 MHz. Override with -DTIMER_CLOCK_HZ=... if needed.
set(TIMER_CLOCK_HZ 10000000 CACHE STRING "Clock feeding TIM2 in Hz")

set(COMMON_DIR ${CMAKE_CURRENT_SOURCE_DIR}/firmware/common)
set(LINKER_SCRIPT ${COMMON_DIR}/stm32f4.ld)

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
    ${COMMON_DIR}/rcc.c
    ${COMMON_DIR}/gpio.c
    ${COMMON_DIR}/uart.c
    ${COMMON_DIR}/timer.c
    ${COMMON_DIR}/can.c
    ${COMMON_DIR}/fmt.c
)
target_include_directories(common PUBLIC ${COMMON_DIR})

# add_firmware(<name> <sources...>) builds an ELF plus .bin and .map next to it.
function(add_firmware name)
    add_executable(${name}.elf ${ARGN})
    target_link_libraries(${name}.elf PRIVATE common gcc)
    target_link_options(${name}.elf PRIVATE -Wl,-Map=${name}.map)
    set_target_properties(${name}.elf PROPERTIES LINK_DEPENDS ${LINKER_SCRIPT})
    add_custom_command(TARGET ${name}.elf POST_BUILD
        COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:${name}.elf> ${name}.bin
        COMMAND ${CMAKE_SIZE} $<TARGET_FILE:${name}.elf>
        COMMENT "Producing ${name}.bin and size report")
endfunction()

add_subdirectory(firmware/app)
add_subdirectory(firmware/can_gateway)
