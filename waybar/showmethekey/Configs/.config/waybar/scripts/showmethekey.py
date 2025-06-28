#!/usr/bin/env python3

"""
  showmethekey-cli | python3 showmethekey.py [options]
  python3 showmethekey.py [options] 
"""

import json
import sys
import argparse
import time
import threading
import subprocess
import signal
import os
import random

# Force unbuffered output for real-time waybar updates
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Global variables for password mode and animation
password_mode = False
password_art_index = 0
current_animation_set = None  # Will be set when password mode is enabled

# Keys that should be blocked from rendering due to parsing issues
BLOCKED_KEYS = {
    "KEY_CAMERA",  # Camera key causes issues with open/close state parsing
    # Add other problematic keys here as needed
}

def handle_sigusr1(signum, frame):
    """Signal handler for SIGUSR1 - toggle password mode"""
    global password_mode, current_animation_set, password_art_index
    password_mode = not password_mode
    
    # When enabling password mode, pick a random animation set
    if password_mode:
        import random
        current_animation_set = random.randint(0, 2)  # 0, 1, or 2
        password_art_index = 0  # Start from first frame

def handle_sigusr2(signum, frame):
    """Signal handler for SIGUSR2 - disable password mode"""
    global password_mode, current_animation_set
    password_mode = False
    current_animation_set = None  # Reset animation set


class EventParser:
    def __init__(
        self, timeout=0.0, max_units=10, min_units=1, waybar=False, mode="compose", wpm_die_time=0.0, gauge=False, rtl=False
    ):
        self.timeout = timeout
        self.max_units = max_units
        self.min_units = min_units
        self.waybar = waybar
        self.gauge = gauge and waybar and wpm_die_time > 0  # Only enable if waybar and WPM are enabled
        self.mode = mode
        self.rtl = rtl
        self.pressed_keys = set()
        self.last_output_time = 0
        self.current_output = ""
        self.accumulated_units = []
        self.lock = threading.Lock()
        self.caps_lock_on = False
        
        # WPM tracking
        self.wpm_tracker = WPMTracker(wpm_die_time) if wpm_die_time > 0 else None

        self.modifier_keys = {
            "LEFTSHIFT",
            "RIGHTSHIFT",
            "LEFTCTRL",
            "RIGHTCTRL",
            "LEFTALT",
            "RIGHTALT",
            "LEFTMETA",
            "RIGHTMETA",
        }

        if self.timeout > 0:
            self.timeout_thread = threading.Thread(
                target=self._timeout_handler, daemon=True
            )
            self.timeout_thread.start()
        
        # Clean up any blocked keys that might be stuck in pressed_keys
        self._cleanup_blocked_keys()

    def _cleanup_blocked_keys(self):
        """Remove any blocked keys from pressed_keys set"""
        self.pressed_keys = {key for key in self.pressed_keys if key not in BLOCKED_KEYS}

    def clean_key_name(self, key_name):
        """Strip KEY_ and BTN_ prefixes and clean up key names based on mode"""
        if not key_name:
            return ""

        clean = key_name
        if key_name.startswith("KEY_"):
            clean = key_name[4:]

        if self.mode == "raw":
            return clean

        special_keys = {
            "LEFTSHIFT": "⇧",
            "RIGHTSHIFT": "⇧",
            "LEFTCTRL": "⌃",
            "RIGHTCTRL": "⌃",
            "LEFTALT": "⌥",
            "RIGHTALT": "⌥",
            "LEFTMETA": " ",
            "RIGHTMETA": " ",
            "CAPSLOCK": "⇪",
            "ENTER": "⏎",
            "SPACE": "␣",
            "TAB": "⇥",
            "BACKSPACE": "⌫",
            "DELETE": "⌦",
            "ESC": "⎋",
            "HOME": "↖",
            "END": "↘",
            "PAGEUP": "⇞",
            "PAGEDOWN": "⇟",
            "INSERT": "⎀",
            "LEFT": "←",
            "RIGHT": "→",
            "UP": "↑",
            "DOWN": "↓",
            "APOSTROPHE": "'",
            "GRAVE": "`",
            "MINUS": "-",
            "EQUAL": "=",
            "LEFTBRACE": "[",
            "RIGHTBRACE": "]",
            "BACKSLASH": "\\",
            "SEMICOLON": ";",
            "COMMA": ",",
            "DOT": ".",
            "SLASH": "/",
            "1": "1",
            "2": "2",
            "3": "3",
            "4": "4",
            "5": "5",
            "6": "6",
            "7": "7",
            "8": "8",
            "9": "9",
            "0": "0",
        }

        special_keys.update(
            {
                "BTN_LEFT": "◀",
                "BTN_RIGHT": "▶",
                "BTN_MIDDLE": "●",
                "BTN_SIDE": "◄",
                "BTN_EXTRA": "►",
                "BTN_FORWARD": "⮞",
                "BTN_BACK": "⮜",
            }
        )

        if clean in special_keys:
            return special_keys[clean]

        if len(clean) == 1 and clean.isalpha():
            shift_pressed = any(
                key in self.pressed_keys for key in ["KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"]
            )

            if shift_pressed ^ self.caps_lock_on:
                return clean.upper()
            else:
                return clean.lower()

        if len(clean) == 1:
            shift_pressed = any(
                key in self.pressed_keys for key in ["KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"]
            )
            if shift_pressed and self.mode == "compact":
                shift_map = {
                    "1": "!",
                    "2": "@",
                    "3": "#",
                    "4": "$",
                    "5": "%",
                    "6": "^",
                    "7": "&",
                    "8": "*",
                    "9": "(",
                    "0": ")",
                    "GRAVE": "~",
                    "MINUS": "_",
                    "EQUAL": "+",
                    "LEFTBRACE": "{",
                    "RIGHTBRACE": "}",
                    "BACKSLASH": "|",
                    "SEMICOLON": ":",
                    "APOSTROPHE": '"',
                    "COMMA": "<",
                    "DOT": ">",
                    "SLASH": "?",
                }
                return shift_map.get(clean, clean)
            else:
                return clean

        if clean.startswith("KP"):
            return clean[2:]

        return clean.title()

    def format_key_combination(self):
        """Format currently pressed keys based on selected mode"""
        if not self.pressed_keys:
            return ""

        modifiers = []
        regular_keys = []

        for key in self.pressed_keys:
            key_without_prefix = key[4:] if key.startswith(("KEY_", "BTN_")) else key

            if key_without_prefix in self.modifier_keys:
                clean_key = self.clean_key_name(key)
                modifiers.append(clean_key)
            else:
                regular_keys.append(key)

        if self.mode == "raw":
            all_keys = []
            for key in self.pressed_keys:
                all_keys.append(self.clean_key_name(key))
            return (
                " + ".join(sorted(all_keys))
                if len(all_keys) > 1
                else (all_keys[0] if all_keys else "")
            )

        elif self.mode == "compact":
            if len(regular_keys) == 1 and len(modifiers) > 0:
                result_char = self.clean_key_name(regular_keys[0])
                return result_char
            elif len(regular_keys) == 1:
                return self.clean_key_name(regular_keys[0])
            elif len(regular_keys) == 0:
                return " + ".join(sorted(set(modifiers)))
            else:
                clean_regular_keys = [self.clean_key_name(key) for key in regular_keys]
                all_keys = sorted(set(modifiers)) + sorted(clean_regular_keys)
                return " + ".join(all_keys)

        else:
            if len(regular_keys) == 0:
                combination = " + ".join(sorted(set(modifiers)))
            elif len(regular_keys) == 1:
                clean_regular = self.clean_key_name(regular_keys[0])
                if modifiers:
                    combination = " + ".join(sorted(set(modifiers)) + [clean_regular])
                else:
                    combination = clean_regular
            else:
                clean_regular_keys = [self.clean_key_name(key) for key in regular_keys]
                all_keys = sorted(set(modifiers)) + sorted(clean_regular_keys)
                combination = " + ".join(all_keys)

            return combination

        if " + " not in combination and len(combination) < self.min_units:
            combination = combination.ljust(self.min_units)

        return combination

    def format_for_waybar(self, text):
        """Format text for waybar with pango markup for modifier highlighting and last key emphasis"""
        if not self.waybar:
            return text

        # Always get WPM tooltip if WPM tracking is enabled
        tooltip = self.get_wpm_tooltip()
        
        if not text:
            # If WPM tracking is enabled, show a space so users can hover for tooltip
            if self.wpm_tracker:
                display_text = " "
            else:
                display_text = ""
            
            if tooltip:
                return json.dumps({"text": display_text, "tooltip": tooltip})
            else:
                return json.dumps({"text": display_text})

        display_text = self.format_accumulated_units(for_waybar=True)
        
        # Add WPM tooltip if available
        result = {"text": display_text}
        if tooltip:
            result["tooltip"] = tooltip
            
        return json.dumps(result)

    def format_accumulated_units(self, for_waybar=False):
        """Format the accumulated units list with counts, optionally with pango markup"""
        if not self.accumulated_units:
            return ""

        # Get the units in the order to display them
        units_to_display = list(self.accumulated_units)
        if self.rtl:
            # For RTL, reverse the order so latest (first in list) appears on the right
            units_to_display.reverse()
        
        current_unit = None
        old_units = []
        
        for i, unit_data in enumerate(units_to_display):
            key = unit_data["key"]
            count = unit_data["count"]

            if count > 1:
                if for_waybar:
                    unit = f'{key}<sup><span weight="bold" style="italic">{count}</span></sup>'
                else:
                    unit = f"{key}^{count}"
            else:
                unit = key

            # For RTL, the "newest" unit for special formatting is the last one in the reversed list
            # For LTR, the "newest" unit is the first one in the original list
            is_newest = (self.rtl and i == len(units_to_display) - 1) or (not self.rtl and i == 0)
            
            if is_newest:
                current_unit = unit
            else:
                old_units.append(unit)

        if not for_waybar:
            # For non-waybar output, keep the original format
            formatted_parts = []
            if current_unit:
                formatted_parts.append(current_unit)
            formatted_parts.extend(old_units)
            return " ".join(formatted_parts)
        
        # For waybar output, format with current unit bold and large, old units in single subscript
        result_parts = []
        
        # Add old units first (if any) wrapped in a single <sub> tag
        if old_units:
            old_text = " ".join(old_units)
            result_parts.append(f'<sub>{old_text}</sub>')
        
        # Add current unit (bold and large)
        if current_unit:
            wpm_color = self.get_wpm_color()
            if wpm_color:
                result_parts.append(
                    f'<span weight="bold" size="x-large" color="{wpm_color}">{current_unit}</span>'
                )
            else:
                result_parts.append(
                    f'<span weight="bold" size="x-large">{current_unit}</span>'
                )
        
        return " ".join(result_parts)

    def get_wpm_tooltip(self):
        """Generate WPM tooltip information"""
        if not self.wpm_tracker:
            return None
            
        stats = self.wpm_tracker.get_wpm_stats()
        
        if stats['session_count'] == 0 and stats['current_wpm'] == 0:
            return "Average WPM: 0\nCharacters: 0\nSessions: 0"
        
        tooltip_lines = []
        
        # Average WPM
        tooltip_lines.append(f"Average WPM: {stats['average_wpm']}")
        
        # Characters (current session)
        tooltip_lines.append(f"Characters: {stats['current_chars']}")
        
        # Session count
        tooltip_lines.append(f"Sessions: {stats['session_count']}")
        
        return "\n".join(tooltip_lines)

    def get_wpm_color(self):
        
        """Get color based on current WPM for gauge visualization"""
        if not self.gauge or not self.wpm_tracker:
            return None
            
        stats = self.wpm_tracker.get_wpm_stats()
        current_wpm = stats['current_wpm']
        
        # Color all typing speeds starting from 30 WPM
        if current_wpm < 30:
            return None
            
        # WPM thresholds for color transitions
        # 30-50 WPM: white → light blue (slow/learning)
        # 50-70 WPM: light blue → light green (average)
        # 70-90 WPM: light green → green (good)
        # 90-110 WPM: green → yellow (fast)
        # 110+ WPM: yellow → red (very fast/expert)
        
        if current_wpm < 50:
            # 30-50: white to light blue
            progress = (current_wpm - 30) / 20
            r = int(255 - (progress * 100))  # 255 → 155
            g = int(255 - (progress * 50))   # 255 → 205
            b = 255
        elif current_wpm < 70:
            # 50-70: light blue to light green
            progress = (current_wpm - 50) / 20
            r = int(155 - (progress * 55))   # 155 → 100
            g = int(205 + (progress * 50))   # 205 → 255
            b = int(255 - (progress * 155))  # 255 → 100
        elif current_wpm < 90:
            # 70-90: light green to green
            progress = (current_wpm - 70) / 20
            r = int(100 - (progress * 50))   # 100 → 50
            g = 255
            b = int(100 - (progress * 50))   # 100 → 50
        elif current_wpm < 110:
            # 90-110: green to yellow
            progress = (current_wpm - 90) / 20
            r = int(50 + (progress * 205))   # 50 → 255
            g = 255
            b = int(50 - (progress * 50))    # 50 → 0
        else:
            # 110+: yellow to red
            progress = min((current_wpm - 110) / 30, 1.0)  # Cap at 140 WPM
            r = 255
            g = int(255 - (progress * 255))  # 255 → 0
            b = 0
            
        return f"#{r:02x}{g:02x}{b:02x}"
    def _timeout_handler(self):
        """Handle timeout to clear output"""
        while True:
            time.sleep(0.1)
            with self.lock:
                # Clean up any blocked keys periodically
                self._cleanup_blocked_keys()
                
                if (
                    self.accumulated_units
                    and time.time() - self.last_output_time >= self.timeout
                ):
                    self.accumulated_units = []
                    self.current_output = ""
                    if self.waybar:
                        print(json.dumps({"text": ""}))
                    else:
                        print("")
                    sys.stdout.flush()

    def process_event(self, event):
        """Process an event and return formatted output"""
        key_name = event.get("key_name", "")
        state_name = event.get("state_name", "")
        
        # Skip processing blocked keys entirely
        if key_name in BLOCKED_KEYS:
            return ""

        with self.lock:
            if state_name == "PRESSED":
                # Track WPM if enabled (only for keyboard keys, not mouse buttons)
                if self.wpm_tracker and not key_name.startswith("BTN_"):
                    is_printable = self.is_printable_key(key_name)
                    self.wpm_tracker.add_keystroke(key_name, is_printable)
                
                self.pressed_keys.add(key_name)

                if key_name == "KEY_CAPSLOCK":
                    self.caps_lock_on = not self.caps_lock_on

                combination = self.format_key_combination()
                if combination:
                    should_replace = False
                    should_increment = False

                    if self.accumulated_units:
                        recent_unit = self.accumulated_units[0]
                        recent_key = recent_unit["key"]

                        if recent_key == combination:
                            should_increment = True

                        elif " + " not in recent_key and " + " in combination:
                            combo_parts = combination.split(" + ")
                            if recent_key in combo_parts:
                                should_replace = True

                        elif " + " in recent_key and " + " in combination:
                            recent_parts = set(recent_key.split(" + "))
                            combo_parts = set(combination.split(" + "))

                            if recent_parts & combo_parts:
                                should_replace = True

                    if should_increment:
                        self.accumulated_units[0]["count"] += 1
                    elif should_replace:
                        self.accumulated_units[0] = {"key": combination, "count": 1}
                    else:
                        self.accumulated_units.insert(
                            0, {"key": combination, "count": 1}
                        )

                        while len(self.accumulated_units) > self.max_units:
                            self.accumulated_units.pop()

                    display_text = self.format_accumulated_units(for_waybar=False)

                    self.current_output = display_text
                    self.last_output_time = time.time()

                    if self.waybar:
                        return self.format_for_waybar(display_text)
                    else:
                        return display_text

            elif state_name == "RELEASED":
                self.pressed_keys.discard(key_name)

                if self.pressed_keys:
                    combination = self.format_key_combination()
                    if combination and " + " in combination:
                        if self.accumulated_units:
                            self.accumulated_units[0] = {"key": combination, "count": 1}
                        else:
                            self.accumulated_units.insert(
                                0, {"key": combination, "count": 1}
                            )
                            while len(self.accumulated_units) > self.max_units:
                                self.accumulated_units.pop()

                        display_text = self.format_accumulated_units(for_waybar=False)
                        self.current_output = display_text
                        self.last_output_time = time.time()

                        if self.waybar:
                            return self.format_for_waybar(display_text)
                        else:
                            return display_text

        return ""

    def is_printable_key(self, key_name):
        """Check if a key represents a printable character for WPM calculation"""
        if not key_name.startswith("KEY_"):
            return False
        
        clean_key = key_name[4:]  # Remove KEY_ prefix
        
        # Exclude modifier keys
        modifier_keys = {
            "LEFTSHIFT", "RIGHTSHIFT", "LEFTCTRL", "RIGHTCTRL",
            "LEFTALT", "RIGHTALT", "LEFTMETA", "RIGHTMETA", "CAPSLOCK"
        }
        if clean_key in modifier_keys:
            return False
        
        # Exclude arrow keys and navigation keys
        navigation_keys = {
            "LEFT", "RIGHT", "UP", "DOWN", "HOME", "END", 
            "PAGEUP", "PAGEDOWN", "INSERT", "DELETE"
        }
        if clean_key in navigation_keys:
            return False
        
        # Exclude function keys (F1, F2, F3, etc.)
        if clean_key.startswith("F") and len(clean_key) > 1 and clean_key[1:].isdigit():
            return False
        
        # Exclude other control keys
        control_keys = {
            "ESC", "BACKSPACE", "PAUSE", "SCROLLLOCK", "NUMLOCK",
            "PRINT", "SYSRQ", "BREAK"
        }
        if clean_key in control_keys:
            return False
        
        # Letters
        if len(clean_key) == 1 and clean_key.isalpha():
            return True
        
        # Numbers
        if clean_key in ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]:
            return True
        
        # Printable symbols
        printable_keys = {
            "SPACE", "APOSTROPHE", "GRAVE", "MINUS", "EQUAL", 
            "LEFTBRACE", "RIGHTBRACE", "BACKSLASH", "SEMICOLON", 
            "COMMA", "DOT", "SLASH", "ENTER", "TAB"
        }
        
        return clean_key in printable_keys


class WPMTracker:
    """Track words per minute with configurable die time"""
    
    def __init__(self, die_time=2.5):
        self.die_time = die_time  # Time in seconds before considering typing session ended
        self.current_session_chars = 0
        self.current_session_start = None
        self.typing_sessions = []  # List of (duration, char_count) tuples
        self.last_keypress_time = None
        self.last_key_pressed = None  # Track last key to prevent spam counting
        self.lock = threading.Lock()
        
    def add_keystroke(self, key_name, is_printable_char=True):
        """Add a keystroke to the current typing session"""
        with self.lock:
            current_time = time.time()
            
            # Skip if this is the same key as the last one (spam prevention)
            if self.last_key_pressed == key_name:
                self.last_keypress_time = current_time
                return
            
            # Check if this is a new session (first keystroke or after die time)
            if (self.last_keypress_time is None or 
                current_time - self.last_keypress_time > self.die_time):
                
                # End previous session if it exists
                if self.current_session_start is not None and self.current_session_chars > 0:
                    session_duration = self.last_keypress_time - self.current_session_start
                    if session_duration > 0:
                        self.typing_sessions.append((session_duration, self.current_session_chars))
                
                # Start new session
                self.current_session_start = current_time
                self.current_session_chars = 0
            
            # Add to current session if it's a printable character
            if is_printable_char:
                self.current_session_chars += 1
            
            self.last_keypress_time = current_time
            self.last_key_pressed = key_name
    
    def get_current_wpm(self):
        """Get WPM for the current active session"""
        with self.lock:
            if self.current_session_start is None or self.current_session_chars == 0:
                return 0.0
            
            current_time = time.time()
            session_duration = current_time - self.current_session_start
            
            if session_duration < 1:  # Less than 1 second, not meaningful
                return 0.0
            
            # 5 characters = 1 word (standard)
            words = self.current_session_chars / 5.0
            minutes = session_duration / 60.0
            
            return words / minutes if minutes > 0 else 0.0
    
    def get_current_chars_per_second(self):
        """Get characters per second for the current active session"""
        with self.lock:
            if self.current_session_start is None or self.current_session_chars == 0:
                return 0.0
            
            current_time = time.time()
            session_duration = current_time - self.current_session_start
            
            if session_duration < 1:  # Less than 1 second, not meaningful
                return 0.0
            
            return self.current_session_chars / session_duration

    def get_average_wpm(self):
        """Get average WPM across all completed sessions"""
        with self.lock:
            if not self.typing_sessions:
                return 0.0
            
            total_words = 0.0
            total_minutes = 0.0
            
            for duration, char_count in self.typing_sessions:
                words = char_count / 5.0
                minutes = duration / 60.0
                total_words += words
                total_minutes += minutes
            
            return total_words / total_minutes if total_minutes > 0 else 0.0
    
    def get_wpm_stats(self):
        """Get comprehensive WPM statistics"""
        current_wpm = self.get_current_wpm()
        average_wpm = self.get_average_wpm()
        chars_per_second = self.get_current_chars_per_second()
        session_count = len(self.typing_sessions)
        
        return {
            'current_wpm': round(current_wpm, 1),
            'average_wpm': round(average_wpm, 1),
            'chars_per_second': round(chars_per_second, 1),
            'session_count': session_count,
            'current_chars': self.current_session_chars
        }


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="ShowMeTheKey - Display keypresses for tutorials"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.0,
        help="Seconds before output vanishes (0.0 to disable, supports decimals like 2.5, default: 0.0)",
    )
    parser.add_argument(
        "--max-units",
        type=int,
        default=3,
        help="Maximum accumulated key units before removing oldest (default: 10)",
    )
    parser.add_argument(
        "--min-units",
        type=int,
        default=1,
        help="Minimum units per individual keypress (default: 1)",
    )
    parser.add_argument(
        "--mode",
        choices=["compact", "compose", "raw"],
        default="compose",
        help="Display mode: compact (show result), compose (show combination), raw (minimal) (default: compose)",
    )
    parser.add_argument(
        "--waybar",
        action="store_true",
        help="Output JSON format for waybar with pango markup for modifier keys",
    )
    parser.add_argument(
        "--gauge",
        action="store_true",
        help="Enable WPM-based color gauge for text (requires --waybar and --wpm). Colors text from white→green→red based on typing speed.",
    )
    parser.add_argument(
        "--password-mode",
        nargs='?',
        const="toggle",
        choices=["0", "1", "toggle"],
        help="Control password mode: 0=off, 1=on, toggle=switch state (default when no value given). Sends signal to running instance and exits.",
    )
    parser.add_argument(
        "--wpm",
        type=float,
        default=0.0,
        help="Enable WPM tracking with specified die time in seconds (e.g., 2.5). 0.0 disables WPM tracking.",
    )
    parser.add_argument(
        "--rtl",
        action="store_true",
        help="Display keystrokes in right-to-left order (latest on right, oldest on left)",
    )

    return parser.parse_args()


def find_and_signal_instances(mode, waybar_output=False):
    """Find running showmethekey.py instances and send appropriate signal"""
    try:
        # Find processes running this script
        result = subprocess.run(['pgrep', '-f', 'showmethekey.py'], 
                              capture_output=True, text=True)
        if result.returncode != 0:
            if not waybar_output:
                print("No running showmethekey.py instances found", file=sys.stderr)
            return False
        
        pids = [int(pid.strip()) for pid in result.stdout.strip().split('\n') if pid.strip()]
        current_pid = os.getpid()
        
        # Filter out current process
        target_pids = [pid for pid in pids if pid != current_pid]
        
        if not target_pids:
            if not waybar_output:
                print("No other showmethekey.py instances found", file=sys.stderr)
            return False
        
        # Determine which signal to send
        if mode in ["1", "toggle"]:
            sig = signal.SIGUSR1  # Toggle password mode
            action = "Enabling" if mode == "1" else "Toggling"
            if not waybar_output:
                print(f"{action} password mode...")
        else:  # mode == "0"
            sig = signal.SIGUSR2  # Disable password mode
            if not waybar_output:
                print("Disabling password mode...")
        
        # Send signal to all instances
        success_count = 0
        for pid in target_pids:
            try:
                os.kill(pid, sig)
                success_count += 1
                if not waybar_output:
                    print(f"Sent signal to PID {pid}")
            except ProcessLookupError:
                if not waybar_output:
                    print(f"Process {pid} no longer exists", file=sys.stderr)
            except PermissionError:
                if not waybar_output:
                    print(f"Permission denied to signal process {pid}", file=sys.stderr)
        
        # For waybar output, show expected state after toggle
        if waybar_output and success_count > 0:
            if mode == "0":
                # Disabled state - show normal keypress indicator
                print(json.dumps({"text": "⌨️"}))
            else:
                # Enabled/toggle state - show password mode indicator  
                print(json.dumps({"text": "🔒 ( &gt; _ &lt; )"}))
        
        return success_count > 0
        
    except Exception as e:
        if not waybar_output:
            print(f"Error finding/signaling processes: {e}", file=sys.stderr)
        return False

def password_art():
    """Generate animated password art - picks one set per session and advances on keystrokes"""
    global password_art_index, current_animation_set
    
    # Set 1: Cats catching butterflies (8-frame story)
    butterfly_catching = [
        "( =^･ω･^)  🦋",           # 1. looking at butterfly
        "( =^･ω･^) 🦋",            # 2. closer
        "ฅ(=^･ω･^=)ฅ 🦋",         # 3. reaching
        "ฅ(=^･ω･^=)🦋",            # 4. almost got it
        "( =^･ω･^) ✨",            # 5. caught it (sparkles)
        "( ˶ᵔ ᵕ ᵔ˶ ) ✨",         # 6. happy with catch
        "( =^･ω･^) 🌸",           # 7. enjoying the moment
        "( ˘ω˘ )ｽﾔｧ 💤",           # 8. satisfied and sleepy
    ]
    
    # Set 2: Dancing and celebration (8-frame story)
    dancing_party = [
        "♪ ヽ(°〇°)ﾉ ♪",           # 1. starting to dance
        "♫ ٩(◕‿◕)۶ ♫",            # 2. getting into rhythm  
        "🎵 ＼(^o^)／ 🎵",         # 3. big celebration
        "✨ (ﾉ◕ヮ◕)ﾉ*:･ﾟ✧",        # 4. magical dance moment
        "🌟 ♪(´▽｀) 🌟",          # 5. singing along
        "💫 ~(˘▾˘)~ 💫",          # 6. graceful swaying
        "🎶 ლ(╹◡╹ლ) 🎶",         # 7. elegant finale
        "✨ (˘▾˘)~ ✨ zzz",        # 8. tired but happy
    ]
    
    # Set 3: Love story (8-frame story)
    kissing_love = [
        "( ˶ᵔ ᵕ ᵔ˶ )",             # 1. shy and happy
        "( ˶ᵔ ᵕ ᵔ˶ ) 💝",          # 2. finding love
        "( ˘ ³˘) 💕",             # 3. preparing kiss
        "( ˘ ³˘)♥ 💕",            # 4. blowing kiss
        "💕 ♥ 💕",                # 5. love in the air
        "✨�✨",                   # 6. love received
        "( ◕ ω ◕ ) 💖",          # 7. glowing with happiness
        "( ˘▾˘)~ 💕💤",           # 8. peaceful and content
    ]
    
    # Collection of all sets
    animation_sets = [butterfly_catching, dancing_party, kissing_love]
    set_names = ["catching butterflies", "dancing party", "love story"]
    
    # If no animation set is chosen yet, pick one (safety fallback)
    if current_animation_set is None:
        import random
        current_animation_set = random.randint(0, 2)
        password_art_index = 0
    
    # Get current frame from the chosen animation set
    current_set = animation_sets[current_animation_set]
    art = current_set[password_art_index % len(current_set)]
    
    # Add activity description for first frame
    if password_art_index == 0:
        activity = set_names[current_animation_set]
        art = f"{art} ({activity})"
    
    return art


def advance_password_art():
    """Advance to the next frame in the password animation"""
    global password_art_index
    password_art_index += 1


def format_password_art_for_waybar(art, wpm_tooltip=None):
    """Format the password art for waybar with pango markup"""
    # Add some styling to make it cute
    colors = ["#ff69b4", "#ffd700", "#98fb98", "#87ceeb", "#dda0dd", "#f0e68c"]
    color = random.choice(colors)
    
    # Combine password mode tooltip with WPM tooltip if available
    tooltip_parts = ["Password mode active - keystrokes are hidden 🔒"]
    if wpm_tooltip:
        tooltip_parts.append(wpm_tooltip)
    
    return json.dumps({
        "text": f'<span weight="bold" size="large" color="{color}">{art}</span>',
        "tooltip": " | ".join(tooltip_parts)
    })  
    

def main():
    """Main function to stream and parse events"""
    args = parse_args()
    
    # Handle password mode control - signal other instances and exit
    if args.password_mode is not None:
        # Check if this is being called from waybar (has waybar flag or is in a module context)
        # We'll output waybar format to provide immediate visual feedback
        success = find_and_signal_instances(args.password_mode, waybar_output=True)
        sys.exit(0 if success else 1)
    
    parser = EventParser(
        timeout=args.timeout,
        max_units=args.max_units,
        min_units=args.min_units,
        waybar=args.waybar,
        mode=args.mode,
        wpm_die_time=args.wpm,
        gauge=args.gauge,
        rtl=args.rtl,
    )
    
    showmethekey_process = None
    
    def cleanup_process():
        """Clean up the showmethekey-cli process"""
        if showmethekey_process and showmethekey_process.poll() is None:
            try:
                showmethekey_process.terminate()
                showmethekey_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                showmethekey_process.kill()
                showmethekey_process.wait()
            except (ProcessLookupError, OSError):
                pass

    def signal_handler(signum, frame):
        """Handle termination signals"""
        cleanup_process()
        sys.exit(0)
    
    # Register signal handlers for proper cleanup and password mode
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGUSR1, handle_sigusr1)
    signal.signal(signal.SIGUSR2, handle_sigusr2)
    
    # Always start showmethekey-cli as subprocess - much simpler and more reliable
    try:
        showmethekey_process = subprocess.Popen(
            ['showmethekey-cli'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0  # Unbuffered
        )
        input_source = showmethekey_process.stdout
    except FileNotFoundError:
        if args.waybar:
            print(json.dumps({"text": "❌ showmethekey-cli not found"}))
        else:
            print("Error: showmethekey-cli not found. Please install it.", file=sys.stderr)
        sys.exit(1)

    # Output initial empty state for waybar
    if args.waybar:
        print(json.dumps({"text": ""}))
        sys.stdout.flush()

    try:
        for line in input_source:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue

            # Check if we're in password mode first
            if password_mode:
                # Still need to parse the event to advance on actual keypresses
                try:
                    event = json.loads(line)
                    key_name = event.get("key_name", "")
                    if (key_name.startswith(("KEY_", "BTN_")) and 
                        key_name not in BLOCKED_KEYS and
                        event.get("state_name", "") == "PRESSED"):
                        # Advance animation frame on each keypress
                        advance_password_art()
                except json.JSONDecodeError:
                    pass
                
                # Display current animation frame
                if args.waybar:
                    # Get WPM tooltip if available
                    wpm_tooltip = parser.get_wpm_tooltip() if parser.wpm_tracker else None
                    art_output = format_password_art_for_waybar(password_art(), wpm_tooltip)
                    print(art_output)
                else:
                    art = password_art()
                    print(art)
                sys.stdout.flush()
                continue  # Skip normal keystroke processing

            try:
                event = json.loads(line)
                if not (event.get("key_name", "").startswith(("KEY_", "BTN_"))):
                    continue
                
                # Filter out problematic keys that can cause parsing issues
                key_name = event.get("key_name", "")
                if key_name in BLOCKED_KEYS:
                    continue

                output = parser.process_event(event)
                if output:
                    print(output)
                    sys.stdout.flush()

            except json.JSONDecodeError:
                continue

    except (KeyboardInterrupt, BrokenPipeError, EOFError):
        pass
    finally:
        cleanup_process()


if __name__ == "__main__":
    main()
