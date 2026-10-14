ENTRY(Reset_Handler)
MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K
         RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 20K }
SECTIONS {
  .text : { KEEP(*(.vectors)) *(.text*) *(.rodata*) } > FLASH
  .state 0x20000000 (NOLOAD) : { *(.state) } > RAM
  .capture 0x20000100 (NOLOAD) : { *(.capture) } > RAM
  .pointers 0x20000380 (NOLOAD) : { *(.pointers) } > RAM
  .rtt 0x20000400 (NOLOAD) : { *(.rtt) } > RAM
  .rttbuffer 0x20000600 (NOLOAD) : { *(.rttbuffer) } > RAM
  .bss (NOLOAD) : { *(.bss*) *(COMMON) } > RAM
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) }
}
