"""A small, complete MCS-51 interpreter for testing K-Board firmware patches.

It executes real firmware bytes. Python callbacks may replace individual
routines (sensor reads, flash writes) so the stock button and note code can
run against scripted inputs. It models the standard 8051 core only: no
peripherals, interrupts or timing. Callers advance any tick counters
themselves.
"""

from __future__ import annotations

ACC, B, PSW, SP, DPL, DPH = 0xE0, 0xF0, 0xD0, 0x81, 0x82, 0x83
SENTINEL = 0xFFF0  # return address that ends a `call`


class Halt(Exception):
    pass


class Machine:
    def __init__(self, code: bytes, xram_size: int = 0x10000):
        self.code = bytearray(code) + bytearray(0x10000 - len(code))
        self.xram = bytearray(xram_size)
        self.iram = bytearray(256)
        self.sfr = bytearray(128)  # index = address - 0x80
        self.pc = 0
        self.sfr[SP - 0x80] = 0x07
        self.stubs = {}
        self.trace = None
        self.steps = 0

    # -- register and memory helpers -------------------------------------
    @property
    def a(self):
        return self.sfr[ACC - 0x80]

    @a.setter
    def a(self, v):
        self.sfr[ACC - 0x80] = v & 0xFF

    @property
    def b(self):
        return self.sfr[B - 0x80]

    @b.setter
    def b(self, v):
        self.sfr[B - 0x80] = v & 0xFF

    @property
    def sp(self):
        return self.sfr[SP - 0x80]

    @sp.setter
    def sp(self, v):
        self.sfr[SP - 0x80] = v & 0xFF

    @property
    def dptr(self):
        return self.sfr[DPH - 0x80] << 8 | self.sfr[DPL - 0x80]

    @dptr.setter
    def dptr(self, v):
        self.sfr[DPH - 0x80] = v >> 8 & 0xFF
        self.sfr[DPL - 0x80] = v & 0xFF

    @property
    def psw(self):
        return self.sfr[PSW - 0x80]

    @psw.setter
    def psw(self, v):
        self.sfr[PSW - 0x80] = v & 0xFF

    @property
    def cy(self):
        return self.psw >> 7 & 1

    @cy.setter
    def cy(self, v):
        self.psw = self.psw & 0x7F | (1 if v else 0) << 7

    @property
    def ov(self):
        return self.psw >> 2 & 1

    @ov.setter
    def ov(self, v):
        self.psw = self.psw & 0xFB | (1 if v else 0) << 2

    def _ac(self, v):
        self.psw = self.psw & 0xBF | (1 if v else 0) << 6

    def _bank(self):
        return (self.psw >> 3 & 3) * 8

    def r(self, n):
        return self.iram[self._bank() + n]

    def set_r(self, n, v):
        self.iram[self._bank() + n] = v & 0xFF

    def get_direct(self, addr):
        if addr < 0x80:
            return self.iram[addr]
        if addr == PSW:  # parity is derived from A
            return self.sfr[PSW - 0x80] & 0xFE | bin(self.a).count("1") & 1
        return self.sfr[addr - 0x80]

    def set_direct(self, addr, v):
        v &= 0xFF
        if addr < 0x80:
            self.iram[addr] = v
        else:
            self.sfr[addr - 0x80] = v

    def get_ind(self, n):
        return self.iram[self.r(n)]

    def set_ind(self, n, v):
        self.iram[self.r(n)] = v & 0xFF

    def _bit_loc(self, bit):
        if bit < 0x80:
            return 0x20 + (bit >> 3), bit & 7
        return bit & 0xF8, bit & 7

    def get_bit(self, bit):
        addr, n = self._bit_loc(bit)
        return self.get_direct(addr) >> n & 1

    def set_bit(self, bit, v):
        addr, n = self._bit_loc(bit)
        cur = self.get_direct(addr)
        self.set_direct(addr, cur & ~(1 << n) | (1 if v else 0) << n)

    def push(self, v):
        self.sp = self.sp + 1
        self.iram[self.sp] = v & 0xFF

    def pop(self):
        v = self.iram[self.sp]
        self.sp = self.sp - 1
        return v

    def push_pc(self, pc):
        self.push(pc & 0xFF)
        self.push(pc >> 8)

    def pop_pc(self):
        hi = self.pop()
        return hi << 8 | self.pop()

    def xget(self, ptr_reg_n):
        return self.xram[(self.sfr[0xA2 - 0x80] << 8 | self.r(ptr_reg_n)) & 0xFFFF]

    # -- arithmetic --------------------------------------------------------
    def _add(self, x, carry_in):
        a = self.a
        total = a + x + carry_in
        self.cy = total > 0xFF
        self._ac((a & 15) + (x & 15) + carry_in > 15)
        self.ov = ((a ^ total) & (x ^ total) & 0x80) != 0
        self.a = total

    def _sub(self, x):
        a = self.a
        total = a - x - self.cy
        self._ac((a & 15) - (x & 15) - self.cy < 0)
        self.ov = ((a ^ x) & (a ^ total) & 0x80) != 0
        self.cy = total < 0
        self.a = total

    # -- execution ---------------------------------------------------------
    def call(self, addr, max_steps=200000, **regs):
        """Run a routine as if LCALLed; returns when it RETs."""
        for name, value in regs.items():
            if name.startswith("r"):
                self.set_r(int(name[1]), value)
            else:
                setattr(self, name, value)
        self.push_pc(SENTINEL)
        self.pc = addr
        start = self.steps
        while self.pc != SENTINEL:
            if self.steps - start > max_steps:
                raise Halt(f"step limit exceeded at 0x{self.pc:04X}")
            self.step()

    def step(self):
        pc = self.pc
        stub = self.stubs.get(pc)
        if stub is not None and stub(self) is not False:
            self.pc = self.pop_pc()  # stub stands in for the whole routine
            return
        code = self.code
        op = code[pc]
        self.steps += 1
        if self.trace:
            self.trace(self, pc, op)
        b1 = code[pc + 1 & 0xFFFF]
        b2 = code[pc + 2 & 0xFFFF]

        def rel(base_len, off):
            return (pc + base_len + (off - 256 if off > 127 else off)) & 0xFFFF

        lo = op & 0x0F
        # AJMP / ACALL
        if lo == 0x01:
            target = (pc + 2 & 0xF800) | (op >> 5) << 8 | b1
            if op & 0x10:
                self.push_pc(pc + 2)
            self.pc = target
            return
        if op == 0x00:
            self.pc = pc + 1
        elif op == 0x02:
            self.pc = b1 << 8 | b2
        elif op == 0x12:
            self.push_pc(pc + 3)
            self.pc = b1 << 8 | b2
        elif op in (0x22, 0x32):
            self.pc = self.pop_pc()
        elif op == 0x03:
            self.a = self.a >> 1 | (self.a & 1) << 7
            self.pc = pc + 1
        elif op == 0x13:
            c = self.a & 1
            self.a = self.a >> 1 | self.cy << 7
            self.cy = c
            self.pc = pc + 1
        elif op == 0x23:
            self.a = self.a << 1 | self.a >> 7
            self.pc = pc + 1
        elif op == 0x33:
            c = self.a >> 7
            self.a = self.a << 1 | self.cy
            self.cy = c
            self.pc = pc + 1
        elif op == 0x04:
            self.a += 1
            self.pc = pc + 1
        elif op == 0x05:
            self.set_direct(b1, self.get_direct(b1) + 1)
            self.pc = pc + 2
        elif op in (0x06, 0x07):
            self.set_ind(lo & 1, self.get_ind(lo & 1) + 1)
            self.pc = pc + 1
        elif 0x08 <= op <= 0x0F:
            self.set_r(lo & 7, self.r(lo & 7) + 1)
            self.pc = pc + 1
        elif op == 0x14:
            self.a -= 1
            self.pc = pc + 1
        elif op == 0x15:
            self.set_direct(b1, self.get_direct(b1) - 1)
            self.pc = pc + 2
        elif op in (0x16, 0x17):
            self.set_ind(lo & 1, self.get_ind(lo & 1) - 1)
            self.pc = pc + 1
        elif 0x18 <= op <= 0x1F:
            self.set_r(lo & 7, self.r(lo & 7) - 1)
            self.pc = pc + 1
        elif op == 0x10:  # JBC
            if self.get_bit(b1):
                self.set_bit(b1, 0)
                self.pc = rel(3, b2)
            else:
                self.pc = pc + 3
        elif op == 0x20:
            self.pc = rel(3, b2) if self.get_bit(b1) else pc + 3
        elif op == 0x30:
            self.pc = pc + 3 if self.get_bit(b1) else rel(3, b2)
        elif op == 0x40:
            self.pc = rel(2, b1) if self.cy else pc + 2
        elif op == 0x50:
            self.pc = pc + 2 if self.cy else rel(2, b1)
        elif op == 0x60:
            self.pc = rel(2, b1) if self.a == 0 else pc + 2
        elif op == 0x70:
            self.pc = pc + 2 if self.a == 0 else rel(2, b1)
        elif op == 0x80:
            self.pc = rel(2, b1)
        elif op in (0x73,):
            self.pc = self.dptr + self.a & 0xFFFF
        elif op in (0x83, 0x93):
            base = pc + 1 if op == 0x83 else self.dptr
            self.a = code[base + self.a & 0xFFFF]
            self.pc = pc + 1
        elif op == 0x90:
            self.dptr = b1 << 8 | b2
            self.pc = pc + 3
        elif op == 0xA3:
            self.dptr = self.dptr + 1
            self.pc = pc + 1
        elif op == 0xE0:
            self.a = self.xram[self.dptr]
            self.pc = pc + 1
        elif op == 0xF0:
            self.xram[self.dptr] = self.a
            self.pc = pc + 1
        elif op in (0xE2, 0xE3):
            self.a = self.xget(lo & 1)
            self.pc = pc + 1
        elif op in (0xF2, 0xF3):
            self.xram[(self.sfr[0xA2 - 0x80] << 8 | self.r(lo & 1)) & 0xFFFF] = self.a
            self.pc = pc + 1
        elif op == 0xA4:
            product = self.a * self.b
            self.a, self.b = product & 0xFF, product >> 8
            self.cy = 0
            self.ov = product > 0xFF
            self.pc = pc + 1
        elif op == 0x84:
            self.cy = 0
            if self.b == 0:
                self.ov = 1
            else:
                self.ov = 0
                q, r = divmod(self.a, self.b)
                self.a, self.b = q, r
            self.pc = pc + 1
        elif op == 0xD4:  # DA A
            a = self.a
            if a & 15 > 9 or self.psw >> 6 & 1:
                a += 6
                if a > 0xFF:
                    self.cy = 1
            if a >> 4 > 9 or self.cy:
                a += 0x60
                if a > 0xFF:
                    self.cy = 1
            self.a = a
            self.pc = pc + 1
        elif op == 0xC4:
            self.a = self.a << 4 | self.a >> 4
            self.pc = pc + 1
        elif op == 0xE4:
            self.a = 0
            self.pc = pc + 1
        elif op == 0xF4:
            self.a = ~self.a
            self.pc = pc + 1
        elif op == 0xC3:
            self.cy = 0
            self.pc = pc + 1
        elif op == 0xD3:
            self.cy = 1
            self.pc = pc + 1
        elif op == 0xB3:
            self.cy = not self.cy
            self.pc = pc + 1
        elif op == 0xC2:
            self.set_bit(b1, 0)
            self.pc = pc + 2
        elif op == 0xD2:
            self.set_bit(b1, 1)
            self.pc = pc + 2
        elif op == 0xB2:
            self.set_bit(b1, not self.get_bit(b1))
            self.pc = pc + 2
        elif op == 0x92:
            self.set_bit(b1, self.cy)
            self.pc = pc + 2
        elif op == 0xA2:
            self.cy = self.get_bit(b1)
            self.pc = pc + 2
        elif op == 0x72:
            self.cy = self.cy | self.get_bit(b1)
            self.pc = pc + 2
        elif op == 0x82:
            self.cy = self.cy & self.get_bit(b1)
            self.pc = pc + 2
        elif op == 0xA0:
            self.cy = self.cy | (not self.get_bit(b1))
            self.pc = pc + 2
        elif op == 0xB0:
            self.cy = self.cy & (not self.get_bit(b1))
            self.pc = pc + 2
        elif op == 0xC0:
            self.push(self.get_direct(b1))
            self.pc = pc + 2
        elif op == 0xD0:
            self.set_direct(b1, self.pop())
            self.pc = pc + 2
        elif op == 0xD5:  # DJNZ direct
            v = self.get_direct(b1) - 1 & 0xFF
            self.set_direct(b1, v)
            self.pc = rel(3, b2) if v else pc + 3
        elif 0xD8 <= op <= 0xDF:
            v = self.r(lo & 7) - 1 & 0xFF
            self.set_r(lo & 7, v)
            self.pc = rel(2, b1) if v else pc + 2
        elif op == 0xB4:
            self.cy = self.a < b1
            self.pc = rel(3, b2) if self.a != b1 else pc + 3
        elif op == 0xB5:
            d = self.get_direct(b1)
            self.cy = self.a < d
            self.pc = rel(3, b2) if self.a != d else pc + 3
        elif op in (0xB6, 0xB7):
            v = self.get_ind(lo & 1)
            self.cy = v < b1
            self.pc = rel(3, b2) if v != b1 else pc + 3
        elif 0xB8 <= op <= 0xBF:
            v = self.r(lo & 7)
            self.cy = v < b1
            self.pc = rel(3, b2) if v != b1 else pc + 3
        elif op == 0xC5:
            d = self.get_direct(b1)
            self.set_direct(b1, self.a)
            self.a = d
            self.pc = pc + 2
        elif op in (0xC6, 0xC7):
            n = lo & 1
            v = self.get_ind(n)
            self.set_ind(n, self.a)
            self.a = v
            self.pc = pc + 1
        elif 0xC8 <= op <= 0xCF:
            n = lo & 7
            v = self.r(n)
            self.set_r(n, self.a)
            self.a = v
            self.pc = pc + 1
        elif op in (0xD6, 0xD7):
            n = lo & 1
            v = self.get_ind(n)
            self.set_ind(n, v & 0xF0 | self.a & 0x0F)
            self.a = self.a & 0xF0 | v & 0x0F
            self.pc = pc + 1
        # MOV group
        elif op == 0x74:
            self.a = b1
            self.pc = pc + 2
        elif op == 0x75:
            self.set_direct(b1, b2)
            self.pc = pc + 3
        elif op in (0x76, 0x77):
            self.set_ind(lo & 1, b1)
            self.pc = pc + 2
        elif 0x78 <= op <= 0x7F:
            self.set_r(lo & 7, b1)
            self.pc = pc + 2
        elif op == 0x85:
            self.set_direct(b2, self.get_direct(b1))
            self.pc = pc + 3
        elif op in (0x86, 0x87):
            self.set_direct(b1, self.get_ind(lo & 1))
            self.pc = pc + 2
        elif 0x88 <= op <= 0x8F:
            self.set_direct(b1, self.r(lo & 7))
            self.pc = pc + 2
        elif op in (0xA6, 0xA7):
            self.set_ind(lo & 1, self.get_direct(b1))
            self.pc = pc + 2
        elif 0xA8 <= op <= 0xAF:
            self.set_r(lo & 7, self.get_direct(b1))
            self.pc = pc + 2
        elif op == 0xE5:
            self.a = self.get_direct(b1)
            self.pc = pc + 2
        elif op in (0xE6, 0xE7):
            self.a = self.get_ind(lo & 1)
            self.pc = pc + 1
        elif 0xE8 <= op <= 0xEF:
            self.a = self.r(lo & 7)
            self.pc = pc + 1
        elif op == 0xF5:
            self.set_direct(b1, self.a)
            self.pc = pc + 2
        elif op in (0xF6, 0xF7):
            self.set_ind(lo & 1, self.a)
            self.pc = pc + 1
        elif 0xF8 <= op <= 0xFF:
            self.set_r(lo & 7, self.a)
            self.pc = pc + 1
        # ALU group: ADD/ADDC/SUBB/ORL/ANL/XRL A,<src> and direct forms
        elif op in (0x42, 0x43, 0x52, 0x53, 0x62, 0x63):
            d = self.get_direct(b1)
            src = self.a if op & 1 == 0 else b2
            fn = {0x40: lambda x, y: x | y, 0x50: lambda x, y: x & y,
                  0x60: lambda x, y: x ^ y}[op & 0xF0]
            self.set_direct(b1, fn(d, src))
            self.pc = pc + (2 if op & 1 == 0 else 3)
        else:
            hi = op & 0xF0
            if hi not in (0x20, 0x30, 0x40, 0x50, 0x60, 0x90) or lo < 4:
                raise Halt(f"unimplemented opcode 0x{op:02X} at 0x{pc:04X}")
            if lo == 4:
                src, length = b1, 2
            elif lo == 5:
                src, length = self.get_direct(b1), 2
            elif lo in (6, 7):
                src, length = self.get_ind(lo & 1), 1
            else:
                src, length = self.r(lo & 7), 1
            if hi == 0x20:
                self._add(src, 0)
            elif hi == 0x30:
                self._add(src, self.cy)
            elif hi == 0x90:
                self._sub(src)
            elif hi == 0x40:
                self.a |= src
            elif hi == 0x50:
                self.a &= src
            else:
                self.a ^= src
            self.pc = pc + length
