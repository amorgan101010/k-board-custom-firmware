#!/usr/bin/env python3
"""Layer the Cyclone timing game onto the sensitivity-menu firmware image."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firmware_tools import build_sensor_config_patch as menus
from firmware_tools import build_velocity_slider_patch as velocity
from firmware_tools.build_relative_tilt_patch import STOCK_SYX
from firmware_tools.extract_kmi_firmware import extract
from firmware_tools.repack_kmi_firmware import pack, records_from_image


# New code goes in erased flash above the menu/preset-save routines.
GAME_DISPATCH = 0xB000
GAME_INPUT = 0xB050
IDLE_INPUT = 0xB100
WAIT_INPUT = 0xB300
PLAY_INPUT = 0xB400
GAME_OVER_INPUT = 0xB600
FAIL_GAME = 0xB680
DRAW_SCORE = 0xB6A0
ENTER_WAIT = 0xB700
START_GAME = 0xB800
MOVE_CURSOR = 0xB900
STOP_GAME = 0xBA00
DRAW_GAME = 0xBB00
WHITE_STEPS = 0xBC00
GOAL_STEPS = 0xBC20
GOAL_LEFT = 0xBC28
GOAL_RIGHT = 0xBC30
MENU_DISPATCH = 0xBD00
MENU_SEND = 0xBE00
GAME_SEND_HOOK = 0xBF00
LED_GATE = 0xBF30
EXIT_WAIT_INPUT = 0xBF60
DRAW_MARKERS = 0xC000
BLINK_CURSOR = 0xC100

GAME_STATE = 0x0F52       # 0 idle, 1 wait for entry chord release, 2 playing, 3 game over
BUTTON_MASK = 0x0F53      # bits: Tilt, Pressure, Velocity
HOLD_ACTIVE = 0x0F54
HOLD_HI = 0x0F55
HOLD_LO = 0x0F56
SAVED_BITS = 0x0F57
SAVED_VALID = 0x0F58
CURSOR = 0x0F59           # white-key step 0..14
TARGET = 0x0F5A           # target white-key step
DIRECTION = 0x0F5C        # 0 descending, 1 ascending
SPEED = 0x0F5D            # cursor interval in ms
LAST_TICK = 0x0F5E        # low byte of last movement tick (interval < 256 ms)
SCORE = 0x0F5F
STOP_HELD = 0x0F60
RNG = 0x0F61
DRAW_FLAG = 0x0F62
BLINK_TICK = 0x0F63        # low-byte tick at the last game-over blink
BLINK_PHASE = 0x0F64       # 1 bright cursor, 0 score-bar level/off
PAD_TOUCH_STATE = 0x0999    # stock Bend Pad scan state; nonzero while touched

PRESS = 0x79
HELD_MIN = 0x40
HOLD_MS = 1000
START_SPEED = 240
MIN_SPEED = 48
SPEED_STEP = 12


def build_dispatch() -> bytes:
    r = velocity.Asm(GAME_DISPATCH)
    r.mov_a_r(7)
    r.mov_r_a(3)
    r.lcall(GAME_INPUT)
    r.rel(0x60, "menu")
    r.ret()
    r.label("menu")
    r.mov_a_r(3)
    r.mov_r_a(7)
    r.ljmp_abs(MENU_DISPATCH)
    return r.finish()


def build_input() -> bytes:
    r = velocity.Asm(GAME_INPUT)
    r.mov_dptr(GAME_STATE)
    r.movx_load()
    r.rel(0x60, "idle")
    r.cjne_a(1, "not_wait")
    r.ljmp_abs(WAIT_INPUT)
    r.label("not_wait")
    r.cjne_a(2, "not_playing")
    r.ljmp_abs(PLAY_INPUT)
    r.label("not_playing")
    r.cjne_a(3, "not_over")
    r.ljmp_abs(GAME_OVER_INPUT)
    r.label("not_over")
    r.ljmp_abs(EXIT_WAIT_INPUT)
    r.label("idle")
    r.ljmp_abs(IDLE_INPUT)
    return r.finish()


def build_idle_input() -> bytes:
    r = velocity.Asm(IDLE_INPUT)
    r.mov_dptr(velocity.XRAM_CHANNEL)
    r.movx_load()
    r.cjne_a(0, "pressure")
    r.mov_a(1)
    r.rel(0x80, "process")
    r.label("pressure")
    r.cjne_a(1, "velocity")
    r.mov_a(2)
    r.rel(0x80, "process")
    r.label("velocity")
    r.cjne_a(4, "not_velocity")
    r.mov_a(4)
    r.rel(0x80, "process")
    r.label("not_velocity")
    r.ljmp("unhandled")
    r.label("process")
    r.mov_r_a(4)                # R4 = channel bit mask
    r.mov_dptr(velocity.XRAM_READING)
    r.movx_load()
    r.mov_r_a(7)                # R7 = current reading
    r.mov_dptr(BUTTON_MASK)
    r.movx_load()
    r.mov_r_a(5)
    # Hysteresis: readings >= PRESS set the bit; readings < HELD_MIN clear it.
    r.mov_a_r(7)
    r.clr_c()
    r.subb(PRESS)
    r.jnc_far("set_bit")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(HELD_MIN)
    r.jc_far("clear_bit")
    r.ljmp("check_chord")
    r.label("set_bit")
    r.mov_a_r(5)
    # OR A,R4
    r.emit(0x4C)
    r.mov_dptr(BUTTON_MASK)
    r.movx_store()
    # Defer the settings snapshot until the mask contains the complete chord.
    r.ljmp("check_chord")
    r.label("clear_bit")
    r.mov_a_r(4)
    r.xrl(0xFF)
    r.mov_r_a(6)
    r.mov_a_r(5)
    r.emit(0x5E)                # ANL A,R6
    r.mov_dptr(BUTTON_MASK)
    r.movx_store()
    r.mov_dptr(HOLD_ACTIVE)
    r.clr_a()
    r.movx_store()
    r.mov_dptr(SAVED_VALID)
    r.clr_a()
    r.movx_store()
    r.mov_dptr(BUTTON_MASK)
    r.movx_load()
    r.rel(0x70, "check_chord")
    r.mov_dptr(SAVED_VALID)
    r.clr_a()
    r.movx_store()
    r.label("check_chord")
    r.mov_dptr(BUTTON_MASK)
    r.movx_load()
    r.cjne_a(0x07, "not_chord")
    r.mov_dptr(HOLD_ACTIVE)
    r.movx_load()
    r.rel(0x70, "elapsed")
    # Capture original button bits at chord start, before a one-second stock
    # toggle can fire during the hold.
    r.mov_dptr(velocity.XRAM_BUTTON_BITS)
    r.movx_load()
    r.anl(0x07)
    r.mov_dptr(SAVED_BITS)
    r.movx_store()
    r.mov_dptr(SAVED_VALID)
    r.mov_a(1)
    r.movx_store()
    r.lcall(velocity.TICK)
    r.mov_dptr(HOLD_HI)
    r.mov_a_r(6)
    r.movx_store()
    r.inc_dptr()
    r.mov_a_r(7)
    r.movx_store()
    r.mov_dptr(HOLD_ACTIVE)
    r.mov_a(1)
    r.movx_store()
    r.ljmp("mask_and_handled")
    r.label("elapsed")
    r.lcall(velocity.TICK)
    r.mov_dptr(HOLD_LO)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_a_r(7)
    r.clr_c()
    r.subb_r(5)
    r.mov_r_a(7)
    r.mov_dptr(HOLD_HI)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_a_r(6)
    r.subb_r(5)
    r.mov_r_a(6)                # R6:R7 elapsed
    r.mov_a_r(6)
    r.cjne_a(0x03, "high_not_equal")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(0xE8)
    r.jnc_far("enter")
    r.ljmp("mask_and_handled")
    r.label("high_not_equal")
    # CJNE sets carry when elapsed high byte is below three.
    r.rel(0x50, "enter")
    r.ljmp("mask_and_handled")
    r.label("enter")
    r.mov_dptr(velocity.XRAM_STATE)
    r.movx_load()
    r.clr_c()
    r.subb(3)
    r.rel(0x40, "enter_now")  # do not interrupt a live sensitivity page/slider
    r.ljmp("mask_and_handled")
    r.label("enter_now")
    r.lcall(ENTER_WAIT)
    r.label("mask_and_handled")
    r.mov_dptr(velocity.XRAM_READING)
    r.clr_a()
    r.movx_store()
    r.mov_a(1)
    r.ret()
    r.label("not_chord")
    r.mov_a(0)
    r.ret()
    r.label("unhandled")
    r.mov_a(0)
    r.ret()
    return r.finish()


def build_wait_input() -> bytes:
    r = velocity.Asm(WAIT_INPUT)
    r.mov_dptr(velocity.XRAM_CHANNEL)
    r.movx_load()
    r.cjne_a(0, "pressure")
    r.mov_a(1)
    r.rel(0x80, "update")
    r.label("pressure")
    r.cjne_a(1, "velocity")
    r.mov_a(2)
    r.rel(0x80, "update")
    r.label("velocity")
    r.cjne_a(4, "mask")
    r.mov_a(4)
    r.label("update")
    r.mov_r_a(4)
    r.mov_dptr(velocity.XRAM_READING)
    r.movx_load()
    r.mov_r_a(7)
    r.mov_a_r(7)
    r.clr_c()
    r.subb(HELD_MIN)
    r.jc_far("released")
    r.mov_dptr(BUTTON_MASK)
    r.movx_load()
    r.emit(0x4C)                # ORL A,R4
    r.movx_store()
    r.ljmp("mask")
    r.label("released")
    r.mov_a_r(4)
    r.xrl(0xFF)
    r.mov_r_a(6)
    r.mov_dptr(BUTTON_MASK)
    r.movx_load()
    r.emit(0x5E)                # ANL A,R6
    r.movx_store()
    r.mov_dptr(BUTTON_MASK)
    r.movx_load()
    r.rel(0x70, "mask")
    r.lcall(velocity.TICK)
    r.mov_dptr(LAST_TICK)
    r.mov_a_r(7)
    r.movx_store()
    r.lcall(START_GAME)         # initializes mode/goal after the entry chord is released
    r.label("mask")
    r.mov_dptr(velocity.XRAM_READING)
    r.clr_a()
    r.movx_store()
    r.mov_a(1)
    r.ret()
    return r.finish()


def build_play_input() -> bytes:
    r = velocity.Asm(PLAY_INPUT)
    r.lcall(MOVE_CURSOR)
    r.mov_dptr(velocity.XRAM_CHANNEL)
    r.movx_load()
    r.cjne_a(0, "exit1")
    r.ljmp("exit_button")
    r.label("exit1")
    r.cjne_a(1, "exit4")
    r.ljmp("exit_button")
    r.label("exit4")
    r.cjne_a(4, "pad_check")
    r.ljmp("exit_button")
    r.label("pad_check")
    r.mov_dptr(PAD_TOUCH_STATE)
    r.movx_load()
    r.rel(0x70, "pad_down")
    r.mov_dptr(STOP_HELD)
    r.clr_a()
    r.movx_store()
    r.ljmp("mask")
    r.label("pad_down")
    r.mov_dptr(STOP_HELD)
    r.movx_load()
    r.rel(0x70, "mask")
    r.mov_a(1)
    r.movx_store()
    r.mov_dptr(CURSOR)
    r.movx_load()
    r.mov_r_a(4)
    r.mov_dptr(TARGET)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_a_r(4)
    r.xrl_r(5)
    r.rel(0x70, "wrong")
    r.ljmp("hit")
    r.label("wrong")
    r.lcall(FAIL_GAME)          # sets state 3 and draws score
    r.ljmp("mask")
    r.label("hit")
    r.mov_dptr(SCORE)
    r.movx_load()
    r.emit(0x04)                # INC A
    r.movx_store()
    r.mov_dptr(SPEED)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_a_r(5)
    r.clr_c()
    r.subb(MIN_SPEED + SPEED_STEP)
    r.jc_far("speed_floor")
    r.mov_a_r(5)
    r.clr_c()
    r.subb(SPEED_STEP)
    r.ljmp("store_speed")
    r.label("speed_floor")
    r.mov_a(MIN_SPEED)
    r.label("store_speed")
    r.movx_store()
    r.lcall(START_GAME)         # new target and direction; preserves score/speed
    r.mov_dptr(STOP_HELD)
    r.mov_a(1)
    r.movx_store()              # Bend Pad must be released before another stop
    r.ljmp("mask")
    r.label("exit_button")
    r.mov_dptr(velocity.XRAM_READING)
    r.movx_load()
    r.clr_c()
    r.subb(PRESS)
    r.jc_far("mask")
    r.lcall(STOP_GAME)
    r.ljmp("mask")
    r.label("mask")
    r.mov_dptr(velocity.XRAM_READING)
    r.clr_a()
    r.movx_store()
    r.mov_a(1)
    r.ret()
    return r.finish()


def build_game_over_input() -> bytes:
    r = velocity.Asm(GAME_OVER_INPUT)
    r.lcall(BLINK_CURSOR)
    r.mov_dptr(velocity.XRAM_CHANNEL)
    r.movx_load()
    r.cjne_a(0, "pressure")
    r.ljmp("exit_button")
    r.label("pressure")
    r.cjne_a(1, "velocity")
    r.ljmp("exit_button")
    r.label("velocity")
    r.cjne_a(4, "sustain")
    r.ljmp("exit_button")
    r.label("sustain")
    r.cjne_a(2, "done")
    r.mov_dptr(velocity.XRAM_READING)
    r.movx_load()
    r.clr_c()
    r.subb(PRESS)
    r.jc_far("done")
    # Restart from level one, retaining the failed target to avoid repeating it.
    r.mov_dptr(GAME_STATE)
    r.mov_a(1)
    r.movx_store()
    r.lcall(START_GAME)
    r.ljmp("done")
    r.label("exit_button")
    r.mov_dptr(velocity.XRAM_READING)
    r.movx_load()
    r.clr_c()
    r.subb(PRESS)
    r.jc_far("done")
    r.lcall(STOP_GAME)
    r.label("done")
    r.mov_dptr(velocity.XRAM_READING)
    r.clr_a()
    r.movx_store()
    r.mov_a(1)
    r.ret()
    return r.finish()


def build_exit_wait_input() -> bytes:
    r = velocity.Asm(EXIT_WAIT_INPUT)
    r.mov_dptr(velocity.XRAM_CHANNEL)
    r.movx_load()
    r.cjne_a(0, "pressure")
    r.mov_a(1)
    r.rel(0x80, "update")
    r.label("pressure")
    r.cjne_a(1, "velocity")
    r.mov_a(2)
    r.rel(0x80, "update")
    r.label("velocity")
    r.cjne_a(4, "mask")
    r.mov_a(4)
    r.label("update")
    r.mov_r_a(4)
    r.mov_dptr(velocity.XRAM_READING)
    r.movx_load()
    r.mov_r_a(7)
    r.mov_a_r(7)
    r.clr_c()
    r.subb(HELD_MIN)
    r.jc_far("released")
    r.mov_dptr(BUTTON_MASK)
    r.movx_load()
    r.emit(0x4C)                # ORL A,R4
    r.movx_store()
    r.ljmp("mask")
    r.label("released")
    r.mov_a_r(4)
    r.xrl(0xFF)
    r.mov_r_a(6)
    r.mov_dptr(BUTTON_MASK)
    r.movx_load()
    r.emit(0x5E)                # ANL A,R6
    r.movx_store()
    r.mov_dptr(BUTTON_MASK)
    r.movx_load()
    r.rel(0x70, "mask")
    r.mov_dptr(GAME_STATE)
    r.clr_a()
    r.movx_store()
    r.mov_dptr(SAVED_VALID)
    r.movx_store()
    r.label("mask")
    r.mov_dptr(velocity.XRAM_READING)
    r.clr_a()
    r.movx_store()
    r.mov_a(1)
    r.ret()
    return r.finish()


def build_start_game() -> bytes:
    r = velocity.Asm(START_GAME)
    # Preserve score and speed when advancing after a successful stop. A fresh
    # entry has state 1 and score zero from reset/idle state.
    r.mov_dptr(GAME_STATE)
    r.movx_load()
    r.cjne_a(1, "level")
    r.mov_dptr(SCORE)
    r.clr_a()
    r.movx_store()
    r.mov_dptr(SPEED)
    r.mov_a(START_SPEED)
    r.movx_store()
    r.label("level")
    r.lcall(velocity.TICK)
    r.mov_dptr(RNG)
    r.movx_load()
    r.xrl_r(7)                  # mix the millisecond tick into the state
    r.mov_r_a(5)
    r.mov_a_r(5)
    r.emit(0x33)                # RLC A: advance the eight-bit Galois LFSR
    r.mov_r_a(6)
    r.rel(0x40, "feedback")     # JC: old high bit was set
    r.mov_a_r(6)
    r.ljmp("mix_tick")
    r.label("feedback")
    r.mov_a_r(6)
    r.xrl(0x1D)                 # x^8 + x^4 + x^3 + x^2 + 1
    r.label("mix_tick")
    r.movx_store()
    r.mov_dptr(RNG)
    r.movx_load()
    r.mov_r_a(5)
    r.anl(0x07)
    r.mov_r_a(4)
    r.clr_c()
    r.subb(6)
    r.jc_far("goal_index")
    r.mov_r_a(4)                # fold random values 6 and 7 into 0 and 1
    r.label("goal_index")
    r.mov_dptr(GOAL_STEPS)
    r.mov_a_r(4)
    r.movc()
    r.mov_r_a(3)
    r.mov_dptr(TARGET)
    r.movx_load()
    r.xrl_r(3)
    r.rel(0x70, "target_ok")
    # If this level repeats the previous target, advance to the next valid one.
    r.inc_r(4)
    r.cjne_r(4, 6, "next_goal")
    r.mov_r(4, 0)
    r.label("next_goal")
    r.mov_dptr(GOAL_STEPS)
    r.mov_a_r(4)
    r.movc()
    r.mov_r_a(3)
    r.label("target_ok")
    r.mov_dptr(TARGET)
    r.mov_a_r(3)
    r.movx_store()
    # Choose direction from the random state; start cursor position comes from
    # the next four bits. Movement always bounces at both ends.
    r.mov_dptr(RNG)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_a_r(5)
    r.emit(0xC4)                # SWAP A
    r.anl(0x01)
    r.mov_dptr(DIRECTION)
    r.movx_store()
    r.mov_a_r(5)
    r.emit(0xC4)                # SWAP A
    r.emit(0x23)                # RL A
    r.anl(0x0F)
    r.cjne_a(15, "cursor_ok")
    r.mov_a(14)
    r.label("cursor_ok")
    r.mov_dptr(CURSOR)
    r.movx_store()
    r.lcall(velocity.TICK)
    r.mov_dptr(LAST_TICK)
    r.mov_a_r(7)
    r.movx_store()
    r.mov_dptr(GAME_STATE)
    r.mov_a(2)
    r.movx_store()
    r.mov_dptr(STOP_HELD)
    r.clr_a()
    r.movx_store()
    r.lcall(DRAW_GAME)
    r.ret()
    return r.finish()


def build_move_cursor() -> bytes:
    r = velocity.Asm(MOVE_CURSOR)
    r.lcall(velocity.TICK)
    r.mov_dptr(LAST_TICK)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_a_r(7)
    r.clr_c()
    r.subb_r(5)
    r.mov_r_a(4)
    r.mov_dptr(SPEED)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_a_r(4)
    r.clr_c()
    r.subb_r(5)
    r.jc_far("done")
    r.lcall(velocity.TICK)
    r.mov_dptr(LAST_TICK)
    r.mov_a_r(7)
    r.movx_store()
    r.mov_dptr(CURSOR)
    r.movx_load()
    r.mov_r_a(4)
    r.mov_dptr(DIRECTION)
    r.movx_load()
    r.rel(0x60, "down")
    # Ascending movement.
    r.inc_r(4)
    r.cjne_r(4, 15, "save")
    r.mov_r(4, 13)
    r.mov_dptr(DIRECTION)
    r.clr_a()
    r.movx_store()
    r.ljmp("save")
    r.label("down")
    r.emit(0x1C)                # DEC R4
    r.mov_a_r(4)
    r.cjne_a(0xFF, "save")
    r.mov_r(4, 1)
    r.mov_dptr(DIRECTION)
    r.mov_a(1)
    r.movx_store()
    r.ljmp("save")
    r.label("save")
    r.mov_dptr(CURSOR)
    r.mov_a_r(4)
    r.movx_store()
    r.lcall(DRAW_GAME)
    r.label("done")
    r.ret()
    return r.finish()


def build_stop_game() -> bytes:
    r = velocity.Asm(STOP_GAME)
    r.mov_dptr(GAME_STATE)
    r.mov_a(4)                  # keep exit button swallowed until release
    r.movx_store()
    r.mov_dptr(BUTTON_MASK)
    r.movx_store()
    r.mov_dptr(HOLD_ACTIVE)
    r.movx_store()
    r.mov_dptr(SAVED_VALID)
    r.movx_load()
    r.rel(0x60, "clear")
    r.mov_dptr(SAVED_BITS)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_dptr(velocity.XRAM_BUTTON_BITS)
    r.movx_load()
    r.mov_r_a(4)
    r.anl(0xF8)
    r.emit(0x4D)                # ORL A,R5
    r.movx_store()
    r.lcall(velocity.STOCK_LOG_STATE)
    r.mov_dptr(SAVED_VALID)
    r.clr_a()
    r.movx_store()
    r.label("clear")
    r.mov_dptr(DRAW_FLAG)
    r.mov_a(1)
    r.movx_store()
    r.mov_r(2, 0)
    r.label("led")
    r.mov_r(7, 0)
    r.mov_a_r(2)
    r.mov_r_a(7)
    r.mov_r(5, 0)
    r.lcall(velocity.STOCK_SET_LED)
    r.inc_r(2)
    r.cjne_r(2, 25, "led")
    r.mov_dptr(DRAW_FLAG)
    r.clr_a()
    r.movx_store()
    r.ret()
    return r.finish()


def build_game_over() -> bytes:
    r = velocity.Asm(FAIL_GAME)
    # This public entry is called by PLAY_INPUT after a bad stop. Its input
    # channel path is also the dispatcher for game-over button presses, so the
    # drawing body is shared through a separate local label below.
    r.mov_dptr(GAME_STATE)
    r.mov_a(3)
    r.movx_store()
    r.lcall(velocity.TICK)
    r.mov_dptr(BLINK_TICK)
    r.mov_a_r(7)
    r.movx_store()
    r.mov_dptr(BLINK_PHASE)
    r.mov_a(1)
    r.movx_store()
    r.lcall(DRAW_SCORE)
    r.ret()
    return r.finish()


def build_draw_score() -> bytes:
    r = velocity.Asm(DRAW_SCORE)
    r.mov_dptr(DRAW_FLAG)
    r.mov_a(1)
    r.movx_store()
    r.mov_r(2, 0)
    r.label("clear")
    r.mov_a_r(2)
    r.mov_r_a(7)
    r.mov_r(5, 0)
    r.lcall(velocity.STOCK_SET_LED)
    r.inc_r(2)
    r.cjne_r(2, 25, "clear")
    r.mov_dptr(SCORE)
    r.movx_load()
    r.mov_r_a(4)
    # The fifteen white LEDs encode a saturated score bar.
    r.mov_a_r(4)
    r.clr_c()
    r.subb(15)
    r.jc_far("score_ok")
    r.mov_r(4, 15)
    r.label("score_ok")
    r.mov_a_r(4)
    r.rel(0x60, "done")
    r.mov_r(2, 0)
    r.label("bar")
    r.mov_dptr(WHITE_STEPS)
    r.mov_a_r(2)
    r.movc()
    r.mov_r_a(7)
    r.mov_r(5, 0x40)
    r.lcall(velocity.STOCK_SET_LED)
    r.inc_r(2)
    r.mov_a_r(2)
    r.clr_c()
    r.subb_r(4)
    r.jc_far("bar")
    r.label("done")
    r.lcall(DRAW_MARKERS)
    r.mov_dptr(DRAW_FLAG)
    r.clr_a()
    r.movx_store()
    r.ret()
    return r.finish()


def build_enter_wait() -> bytes:
    r = velocity.Asm(ENTER_WAIT)
    r.mov_dptr(GAME_STATE)
    r.mov_a(1)
    r.movx_store()
    r.mov_dptr(TARGET)
    r.mov_a(0xFF)
    r.movx_store()
    # A chord takes priority over a sensitivity hold that matured in this scan.
    r.mov_dptr(velocity.XRAM_STATE)
    r.clr_a()
    r.movx_store()
    r.mov_dptr(SAVED_BITS)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_dptr(velocity.XRAM_BUTTON_BITS)
    r.movx_load()
    r.anl(0xF8)
    r.emit(0x4D)                # ORL A,R5
    r.movx_store()
    r.lcall(velocity.STOCK_LOG_STATE)
    r.mov_dptr(DRAW_FLAG)
    r.mov_a(1)
    r.movx_store()
    r.mov_r(2, 0)
    r.label("clear")
    r.mov_a_r(2)
    r.mov_r_a(7)
    r.mov_r(5, 0)
    r.lcall(velocity.STOCK_SET_LED)
    r.inc_r(2)
    r.cjne_r(2, 25, "clear")
    r.mov_dptr(DRAW_FLAG)
    r.clr_a()
    r.movx_store()
    r.ret()
    return r.finish()


def build_draw_game() -> bytes:
    r = velocity.Asm(DRAW_GAME)
    # Clear all key LEDs, then draw the cursor and the two black-key goal posts.
    r.mov_dptr(DRAW_FLAG)
    r.mov_a(1)
    r.movx_store()
    r.mov_r(2, 0)
    r.label("clear")
    r.mov_a_r(2)
    r.mov_r_a(7)
    r.mov_r(5, 0)
    r.lcall(velocity.STOCK_SET_LED)
    r.inc_r(2)
    r.cjne_r(2, 25, "clear")
    r.lcall(DRAW_MARKERS)
    r.mov_dptr(DRAW_FLAG)
    r.clr_a()
    r.movx_store()
    r.ret()
    return r.finish()


def build_draw_markers() -> bytes:
    r = velocity.Asm(DRAW_MARKERS)
    # The cursor is drawn over the score bar; game-over polling blinks it
    # between full brightness and fully off.
    r.mov_dptr(CURSOR)
    r.movx_load()
    r.mov_r_a(4)
    r.mov_dptr(WHITE_STEPS)
    r.mov_a_r(4)
    r.movc()
    r.mov_r_a(7)
    r.mov_r(5, 0xFF)
    r.lcall(velocity.STOCK_SET_LED)
    # Target ordinal is recovered by finding the goal step in GOAL_STEPS.
    r.mov_dptr(TARGET)
    r.movx_load()
    r.mov_r_a(4)
    r.mov_r(2, 0)
    r.label("find")
    r.mov_dptr(GOAL_STEPS)
    r.mov_a_r(2)
    r.movc()
    r.xrl_r(4)
    r.rel(0x70, "next")
    r.ljmp("posts")
    r.label("next")
    r.inc_r(2)
    r.cjne_r(2, 6, "find")
    r.ljmp("done")
    r.label("posts")
    r.mov_dptr(GOAL_LEFT)
    r.mov_a_r(2)
    r.movc()
    r.mov_r_a(7)
    r.mov_r(5, 0xFF)
    r.lcall(velocity.STOCK_SET_LED)
    r.mov_dptr(GOAL_RIGHT)
    r.mov_a_r(2)
    r.movc()
    r.mov_r_a(7)
    r.mov_r(5, 0xFF)
    r.lcall(velocity.STOCK_SET_LED)
    r.label("done")
    r.ret()
    return r.finish()


def build_blink_cursor() -> bytes:
    r = velocity.Asm(BLINK_CURSOR)
    r.lcall(velocity.TICK)
    r.mov_dptr(BLINK_TICK)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_a_r(7)
    r.clr_c()
    r.subb_r(5)
    r.clr_c()
    r.subb(250)                 # phase change about every 250 ms
    r.jc_far("done")
    r.mov_dptr(BLINK_TICK)
    r.mov_a_r(7)
    r.movx_store()
    r.mov_dptr(BLINK_PHASE)
    r.movx_load()
    r.xrl(1)
    r.movx_store()
    r.mov_dptr(DRAW_FLAG)
    r.mov_a(1)
    r.movx_store()
    r.mov_dptr(CURSOR)
    r.movx_load()
    r.mov_r_a(4)
    r.mov_dptr(WHITE_STEPS)
    r.mov_a_r(4)
    r.movc()
    r.mov_r_a(7)
    r.mov_dptr(BLINK_PHASE)
    r.movx_load()
    r.rel(0x70, "bright")
    # Blank the cursor unconditionally. If it overlaps the score bar, that
    # LED will briefly disappear too, which is necessary for a visible blink.
    r.mov_r(5, 0)
    r.ljmp("set_level")
    r.label("bright")
    r.mov_r(5, 0xFF)
    r.label("set_level")
    r.lcall(velocity.STOCK_SET_LED)
    r.mov_dptr(DRAW_FLAG)
    r.clr_a()
    r.movx_store()
    r.label("done")
    r.ret()
    return r.finish()


def build_tables() -> dict[int, bytes]:
    return {
        WHITE_STEPS: bytes((0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23, 24)),
        # Only use black-key pairs with exactly one natural note between them.
        # D#/F# and A#/C# each bracket two natural notes, so neither is a
        # clean single-key target.
        GOAL_STEPS: bytes((1, 4, 5, 8, 11, 12)),
        GOAL_LEFT: bytes((1, 6, 8, 13, 18, 20)),
        GOAL_RIGHT: bytes((3, 8, 10, 15, 20, 22)),
    }


def build_game_send_hook() -> bytes:
    r = velocity.Asm(GAME_SEND_HOOK)
    r.mov_dptr(GAME_STATE)
    r.movx_load()
    r.rel(0x60, "menu")
    # In the game, suppress channel messages from note-on through pitch bend.
    r.mov_a_r(7)
    r.clr_c()
    r.subb(0x90)
    r.rel(0x40, "menu")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(0xF0)
    r.rel(0x50, "menu")
    r.ret()
    r.label("menu")
    r.ljmp_abs(MENU_SEND)
    return r.finish()


def build_led_gate() -> bytes:
    r = velocity.Asm(LED_GATE)
    r.emit(0xC0, 0xE0, 0xC0, 0x82, 0xC0, 0x83)  # preserve A and the table DPTR
    r.mov_dptr(GAME_STATE)
    r.movx_load()
    r.rel(0x60, "allow")
    r.mov_dptr(DRAW_FLAG)
    r.movx_load()
    r.rel(0x60, "drop")
    r.label("allow")
    r.emit(0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xE0)
    r.lcall(0x6BC3)             # displaced call at 0x7B26
    r.ljmp_abs(0x7B29)          # continue the stock LED-level writer
    r.label("drop")
    r.emit(0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xE0)
    r.ljmp_abs(0x7B2E)          # return from the stock LED setter
    return r.finish()


def _store(image: bytearray, address: int, code: bytes) -> None:
    if image[address:address + len(code)] != b"\xff" * len(code):
        raise ValueError(f"game code region at 0x{address:04X} is occupied")
    image[address:address + len(code)] = code


def build_image(base_image: bytes) -> tuple[bytes, bytes]:
    """Apply checked game hooks to the sensitivity-menu image."""
    patched = bytearray(base_image)
    expected_dispatch = menus.build_dispatch()
    menu_dispatch = bytes(patched[menus.DISPATCH:menus.DISPATCH + len(expected_dispatch)])
    menu_send = menus.build_send_hook()
    if menu_dispatch != expected_dispatch:
        raise ValueError("menu dispatcher body differs from its generated form")
    if bytes(patched[velocity.SEND_HOOK:velocity.SEND_HOOK + len(menu_send)]) != menu_send:
        raise ValueError("menu MIDI helper body differs from its generated form")
    _store(patched, MENU_DISPATCH, menu_dispatch)
    _store(patched, MENU_SEND, menu_send)

    # Replace the existing menu dispatch and MIDI helper entries with game-aware
    # wrappers. Hooks are intentionally limited to those two integration points.
    if bytes(patched[menus.DISPATCH:menus.DISPATCH + 3]) != bytes((0x90, 0x0F, 0x40)):
        raise ValueError("menu dispatcher entry bytes differ")
    if bytes(patched[velocity.SEND_HOOK:velocity.SEND_HOOK + 3]) != menu_send[:3]:
        raise ValueError("menu MIDI helper entry bytes differ")
    if bytes(patched[0x7B26:0x7B29]) != bytes.fromhex("12 6b c3"):
        raise ValueError("stock key-LED update bytes differ")
    patched[menus.DISPATCH:menus.DISPATCH + 3] = bytes((0x02, GAME_DISPATCH >> 8, GAME_DISPATCH & 255))
    patched[velocity.SEND_HOOK:velocity.SEND_HOOK + 3] = bytes((0x02, GAME_SEND_HOOK >> 8, GAME_SEND_HOOK & 255))
    patched[0x7B26:0x7B29] = bytes((0x02, LED_GATE >> 8, LED_GATE & 255))

    routines = {
        GAME_DISPATCH: build_dispatch(),
        GAME_INPUT: build_input(),
        IDLE_INPUT: build_idle_input(),
        WAIT_INPUT: build_wait_input(),
        PLAY_INPUT: build_play_input(),
        GAME_OVER_INPUT: build_game_over_input(),
        EXIT_WAIT_INPUT: build_exit_wait_input(),
        FAIL_GAME: build_game_over(),
        DRAW_SCORE: build_draw_score(),
        ENTER_WAIT: build_enter_wait(),
        START_GAME: build_start_game(),
        MOVE_CURSOR: build_move_cursor(),
        STOP_GAME: build_stop_game(),
        DRAW_GAME: build_draw_game(),
        DRAW_MARKERS: build_draw_markers(),
        BLINK_CURSOR: build_blink_cursor(),
        GAME_SEND_HOOK: build_game_send_hook(),
        LED_GATE: build_led_gate(),
        **build_tables(),
    }
    spans = sorted((a, a + len(c)) for a, c in routines.items())
    for (_, end), (start, _) in zip(spans, spans[1:]):
        if end > start:
            raise ValueError(f"Cyclone regions overlap near 0x{start:04X}")
    for address, code in routines.items():
        _store(patched, address, code)

    # Repack the complete stock-derived image; firmware_tools never flashes it.
    records, _ = extract(STOCK_SYX)
    return bytes(patched), pack(records_from_image(records, patched))
