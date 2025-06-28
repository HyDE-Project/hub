#!/usr/bin/env python3
"""
OpenTabletDriver Preset Switcher for Waybar

A simple preset switcher that cycles through available presets using clicks.
Displays current tablet name, active preset, and available presets in tooltip.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional


class TabletSettings:
    """Parse and store current tablet settings from otd getallsettings"""
    
    def __init__(self, otd_output: str):
        self.tablet_name = None
        self.output_mode = None
        self.output_mode_path = None
        self.tip_binding = None
        self.pen_bindings = []
        self.express_bindings = []
        self.display_area = None
        self.tablet_area = None
        
        # Store parsed binding structures for better matching
        self.parsed_pen_bindings = []
        self.parsed_express_bindings = []
        
        self._parse_otd_output(otd_output)
    
    def _parse_binding(self, binding_str: str) -> Dict:
        """Parse a binding string into a structured format"""
        binding_info = {}
        
        if "Key Binding: { Key:" in binding_str:
            # Single key binding
            key = binding_str.split("Key: ")[1].split(" }")[0]
            binding_info = {"type": "key", "key": key}
        elif "Multi-Key Binding: { Keys:" in binding_str:
            # Multi-key binding
            keys = binding_str.split("Keys: ")[1].split(" }")[0]
            binding_info = {"type": "multi_key", "keys": keys}
        elif "Button: Pen Button" in binding_str:
            # Pen button binding
            button = binding_str.split("Button: ")[1].split(" }")[0]
            binding_info = {"type": "pen_button", "button": button}
        elif "Linux Artist Mode:" in binding_str:
            # Artist mode specific binding
            inner = binding_str.split("Linux Artist Mode: { ")[1].split(" }")[0]
            if "Button:" in inner:
                button = inner.split("Button: ")[1]
                binding_info = {"type": "artist_button", "button": button}
        
        return binding_info
    
    def _parse_otd_output(self, output: str):
        """Parse the otd getallsettings output"""
        lines = output.split('\n')
        
        for line in lines:
            line = line.strip()
            
            if line.startswith("--- Profile for '") and line.endswith("' ---"):
                self.tablet_name = line[17:-5]
            elif line.startswith("Output Mode: '") and line.endswith("'"):
                self.output_mode = line[14:-1]
                # Map output mode to expected JSON path
                mode_mapping = {
                    'Artist Mode': 'OpenTabletDriver.Desktop.Output.LinuxArtistMode',
                    'Absolute Mode': 'OpenTabletDriver.Desktop.Output.AbsoluteMode',
                    'Relative Mode': 'OpenTabletDriver.Desktop.Output.RelativeMode'
                }
                self.output_mode_path = mode_mapping.get(self.output_mode, '')
            elif line.startswith("Tip Binding: "):
                self.tip_binding = line[13:]
            elif line.startswith("Pen Bindings: "):
                pen_bindings_str = line[14:]
                if pen_bindings_str and pen_bindings_str != 'None':
                    self.pen_bindings = [b.strip().strip("'") for b in pen_bindings_str.split("', '")]
                    # Parse pen bindings for better matching
                    for binding in self.pen_bindings:
                        parsed = self._parse_binding(binding)
                        if parsed:
                            self.parsed_pen_bindings.append(parsed)
            elif line.startswith("Express Key Bindings: "):
                express_bindings_str = line[22:]
                if express_bindings_str and express_bindings_str != 'None':
                    self.express_bindings = [b.strip().strip("'") for b in express_bindings_str.split("', '")]
                    # Parse express key bindings for better matching
                    for binding in self.express_bindings:
                        parsed = self._parse_binding(binding)
                        if parsed:
                            self.parsed_express_bindings.append(parsed)
            elif line.startswith("Display area: "):
                self.display_area = line[14:]
            elif line.startswith("Tablet area: "):
                self.tablet_area = line[13:]


class PresetMatcher:
    """Match current tablet settings with preset JSON files"""
    
    def __init__(self, presets_dir: Path):
        self.presets_dir = presets_dir
        self.preset_cache = {}
    
    def load_preset_data(self, preset_name: str) -> Optional[Dict]:
        """Load and cache preset JSON data"""
        if preset_name in self.preset_cache:
            return self.preset_cache[preset_name]
        
        preset_file = self.presets_dir / f"{preset_name}.json"
        if not preset_file.exists():
            return None
        
        try:
            with open(preset_file, 'r') as f:
                data = json.load(f)
                self.preset_cache[preset_name] = data
                return data
        except (json.JSONDecodeError, IOError):
            return None
    
    def get_preset_bindings(self, preset_name: str) -> Dict:
        """Extract binding information from a preset JSON file"""
        data = self.load_preset_data(preset_name)
        if not data:
            return {}
        
        try:
            profile = data['Profiles'][0]
            bindings = profile.get('Bindings', {})
            bindings_info = {
                'pen_bindings': [],
                'express_bindings': [],
                'tip_binding': None
            }
            
            # Extract pen bindings from PenButtons array
            pen_buttons = bindings.get('PenButtons', [])
            for pen_button in pen_buttons:
                if pen_button and pen_button.get('Enable', False):
                    # Extract the actual key/button value, not just the path
                    settings = pen_button.get('Settings', [])
                    for setting in settings:
                        if setting.get('Property') in ['Button', 'Key', 'Keys']:
                            value = setting.get('Value', '')
                            if value:
                                bindings_info['pen_bindings'].append(value)
            
            # Extract express key bindings from AuxButtons array
            aux_buttons = bindings.get('AuxButtons', [])
            for aux_button in aux_buttons:
                if aux_button and aux_button.get('Enable', False):
                    # Extract the actual key value, not just the path
                    settings = aux_button.get('Settings', [])
                    for setting in settings:
                        if setting.get('Property') in ['Key', 'Keys']:
                            value = setting.get('Value', '')
                            if value:
                                bindings_info['express_bindings'].append(value)
            
            # Extract tip binding from TipButton
            tip_button = bindings.get('TipButton', {})
            if tip_button and tip_button.get('Enable', False):
                settings = tip_button.get('Settings', [])
                for setting in settings:
                    if setting.get('Property') in ['Button', 'Key']:
                        value = setting.get('Value', '')
                        if value:
                            bindings_info['tip_binding'] = value
            
            return bindings_info
            
        except (KeyError, IndexError):
            return {}
    
    def calculate_preset_match_score(self, current_settings: TabletSettings, preset_name: str) -> float:
        """Calculate how well a preset matches the current settings (0.0 to 1.0)"""
        preset_output_mode = self.get_preset_output_mode_path(preset_name)
        preset_bindings = self.get_preset_bindings(preset_name)
        
        score = 0.0
        total_weight = 0.0
        
        # Output mode matching (weight: 0.3)
        if preset_output_mode and current_settings.output_mode_path:
            if preset_output_mode == current_settings.output_mode_path:
                score += 0.3
            total_weight += 0.3
        
        # Express key bindings matching (weight: 0.5) - higher weight since this is the main differentiator
        express_weight = 0.5
        current_express_keys = set()
        preset_express_keys = set(preset_bindings.get('express_bindings', []))
        
        # Extract actual keys from current settings
        for parsed_binding in current_settings.parsed_express_bindings:
            if parsed_binding.get('type') == 'key':
                current_express_keys.add(parsed_binding.get('key', ''))
            elif parsed_binding.get('type') == 'multi_key':
                current_express_keys.add(parsed_binding.get('keys', ''))
        
        if preset_express_keys or current_express_keys:
            # Calculate overlap between key sets
            overlap = len(preset_express_keys & current_express_keys)
            total_keys = len(preset_express_keys | current_express_keys)
            
            if total_keys > 0:
                # Score based on how many keys match vs total unique keys
                express_score = overlap / total_keys
                score += express_weight * express_score
            elif len(preset_express_keys) == 0 and len(current_express_keys) == 0:
                # Both have no express keys - perfect match
                score += express_weight
            total_weight += express_weight
        
        # Pen bindings matching (weight: 0.2) - lower weight since they seem more similar across presets
        pen_weight = 0.2
        current_pen_buttons = set()
        preset_pen_buttons = set(preset_bindings.get('pen_bindings', []))
        
        # Extract actual button values from current settings
        for parsed_binding in current_settings.parsed_pen_bindings:
            if parsed_binding.get('type') in ['pen_button', 'artist_button']:
                current_pen_buttons.add(parsed_binding.get('button', ''))
        
        if preset_pen_buttons or current_pen_buttons:
            # Calculate overlap between button sets
            overlap = len(preset_pen_buttons & current_pen_buttons)
            total_buttons = len(preset_pen_buttons | current_pen_buttons)
            
            if total_buttons > 0:
                pen_score = overlap / total_buttons
                score += pen_weight * pen_score
            elif len(preset_pen_buttons) == 0 and len(current_pen_buttons) == 0:
                # Both have no pen buttons - perfect match
                score += pen_weight
            total_weight += pen_weight
        
        # Return normalized score
        return score / total_weight if total_weight > 0 else 0.0
    
    def get_preset_output_mode_path(self, preset_name: str) -> Optional[str]:
        """Get the output mode path from a preset JSON file"""
        data = self.load_preset_data(preset_name)
        if not data:
            return None
        
        try:
            return data['Profiles'][0]['OutputMode']['Path']
        except (KeyError, IndexError):
            return None

    def find_matching_preset(self, current_settings: TabletSettings, available_presets: List[str]) -> str:
        """Find which preset best matches the current settings using comprehensive scoring"""
        if not available_presets:
            # If no presets available, try to return something meaningful from the output mode
            if current_settings.output_mode:
                return current_settings.output_mode.split()[0]
            return "No Presets"
        
        best_preset = available_presets[0]  # Default to first preset if nothing matches
        best_score = 0.0
        
        # Calculate match score for each preset
        for preset in available_presets:
            score = self.calculate_preset_match_score(current_settings, preset)
            if score > best_score:
                best_score = score
                best_preset = preset
        
        # If no preset has a good match (score < 0.5), fall back to output mode name matching
        if best_score < 0.5 and current_settings.output_mode:
            for preset in available_presets:
                if preset.lower() in current_settings.output_mode.lower():
                    return preset
            # Try to match based on output mode keywords
            mode_lower = current_settings.output_mode.lower()
            for preset in available_presets:
                preset_lower = preset.lower()
                if ("artist" in mode_lower and "artist" in preset_lower) or \
                   ("absolute" in mode_lower and "abs" in preset_lower) or \
                   ("relative" in mode_lower and "rel" in preset_lower):
                    return preset
        
        # Always return the best matching preset (never "Unknown")
        return best_preset


class WaybarFormat:
    """Format OpenTabletDriver data for Waybar output"""
    
    def __init__(self, preset_switcher):
        self.preset_switcher = preset_switcher
    
    def _get_output_mode_icon(self, output_mode: str) -> str:
        """Get icon for output mode"""
        if not output_mode:
            return "󰏘"  # Default icon for unknown mode
            
        mode_lower = output_mode.lower()
        
        if "artist" in mode_lower:
            return "󰏘"
        elif "absolute" in mode_lower:
            return ""
        elif "relative" in mode_lower:
            return "󰌌"
        else:
            return "󰏘"
    
    def _format_bindings(self, settings: TabletSettings) -> List[str]:
        """Format bindings for tooltip"""
        binding_lines = []
        
        def clean_binding(binding_str):
            """Extract clean action from binding string"""
            if "Key: " in binding_str:
                key = binding_str.split("Key: ")[1].split(" }")[0]
                return key.replace("Left", "").replace("Control", "Ctrl")
            elif "Keys: " in binding_str:
                keys = binding_str.split("Keys: ")[1].split(" }")[0]
                return keys.replace("Control", "Ctrl")
            elif "Button: " in binding_str:
                button = binding_str.split("Button: ")[1].split(" }")[0]
                return button.replace("Pen Button ", "Btn")
            return binding_str.strip()
        
        # Tip binding
        if settings.tip_binding and settings.tip_binding not in ['None', 'Error'] and any(kw in settings.tip_binding for kw in ['Key:', 'Button:', 'Keys:']):
            tip = settings.tip_binding
            if "@" in tip:
                action, threshold = tip.rsplit("@", 1)
                clean_action = clean_binding(action)
                binding_lines.extend(["<b>Tip:</b>", f"      {clean_action} (at {threshold})"])
            else:
                clean_action = clean_binding(tip)
                binding_lines.extend(["<b>Tip:</b>", f"      {clean_action}"])
        
        # Pen buttons
        if settings.pen_bindings:
            binding_lines.append("<b>Pen Buttons:</b>")
            for binding in settings.pen_bindings:
                action = clean_binding(binding)
                binding_lines.append(f"      • {action}")
        
        # Express keys
        if settings.express_bindings:
            binding_lines.append("<b>Express Keys:</b>")
            for binding in settings.express_bindings:
                action = clean_binding(binding)
                binding_lines.append(f"      • {action}")
        
        return binding_lines
    
    def get_waybar_output(self) -> Dict:
        """Get Waybar output format"""
        presets = self.preset_switcher.list_presets()
        current_settings = self.preset_switcher.get_current_settings(for_waybar=True)
        
        if not presets:
            return {
                "text": "<b>󰏘 No Presets</b>",
                "tooltip": "No OpenTabletDriver presets found",
                "class": "error"
            }
        
        if not current_settings:
            # Show error from getallsettings in tooltip
            error_tooltip = "Failed to get tablet settings after multiple retries"
            if self.preset_switcher.last_error:
                error_tooltip += f"\n\nError: {self.preset_switcher.last_error}"
            
            return {
                "text": "<b>󰔟 Error</b>",
                "tooltip": error_tooltip,
                "class": "error"
            }
        
        # Get current preset name by matching settings
        current_preset = self.preset_switcher.matcher.find_matching_preset(current_settings, presets)
        
        # Get icon and create display text (handle None values gracefully)
        output_mode = current_settings.output_mode or "Unknown"
        tablet_name = current_settings.tablet_name or "Unknown Tablet"
        
        icon = self._get_output_mode_icon(output_mode)
        compact_text = f"<b>{icon} <sup><small>{current_preset}</small></sup></b>"
        
        # Create tooltip
        tooltip_lines = [
            f"<b><big>{current_preset}</big></b>",
            "",
            f"Tablet: {tablet_name}",
            f"Mode: {output_mode}",
            "",
        ]
        
        # Add bindings
        binding_lines = self._format_bindings(current_settings)
        if binding_lines:
            tooltip_lines.extend(binding_lines)
            tooltip_lines.append("")
        
        # Add preset list
        tooltip_lines.append("Presets:")
        for preset in presets:
            if preset == current_preset:
                tooltip_lines.append(f"  <b>{preset}</b>")
            else:
                tooltip_lines.append(f"  {preset}")
        
        tooltip_lines.extend(["", "Click to cycle forward"])
        
        return {
            "text": compact_text,
            "tooltip": "\n".join(tooltip_lines),
            "class": "normal"
        }


class OpenTabletDriverPresetSwitcher:
    """Main class for handling preset switching"""
    
    def __init__(self):
        self.config_dir = self._get_config_dir()
        self.presets_dir = self.config_dir / "OpenTabletDriver" / "Presets"
        self.matcher = PresetMatcher(self.presets_dir)
        self.waybar_formatter = WaybarFormat(self)
        self.last_error = None  # Store last error for display in tooltip
        self._cached_settings = None  # Cache the settings so we only call otd once
        self._settings_fetched = False  # Track if we've already tried to fetch settings
        
    def _get_config_dir(self) -> Path:
        """Get the XDG config directory."""
        xdg_config = os.environ.get('XDG_CONFIG_HOME')
        if xdg_config:
            return Path(xdg_config)
        return Path.home() / ".config"
    
    def _run_otd_command(self, command: List[str], timeout: int = 10, retries: int = 3) -> Optional[str]:
        """Run an otd command with simple retry logic"""
        self.last_error = None  # Clear previous errors
        
        # For getallsettings, be more persistent since we never want to show "Unknown"
        if command and command[0] == 'getallsettings':
            retries = 5
            timeout = 15
        
        for attempt in range(retries):
            try:
                result = subprocess.run(
                    ['otd'] + command,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=timeout
                )
                
                # Check if output contains connection errors even with exit code 0
                output = result.stdout.strip()
                
                # For getallsettings, verify we got valid tablet information
                if command and command[0] == 'getallsettings':
                    if not output or "--- Profile for" not in output:
                        if attempt < retries - 1:  # Not the last attempt
                            time.sleep(6.0)  # Wait 6 seconds before retry
                            continue
                        self.last_error = "OpenTabletDriver returned incomplete settings"
                        return None
                
                # Success - return the output
                return output
                
            except subprocess.TimeoutExpired:
                if attempt < retries - 1:  # Not the last attempt
                    time.sleep(6.0)  # Wait 6 seconds before retry
                    continue
                self.last_error = f"OpenTabletDriver timeout after {retries} attempts"
                
            except subprocess.CalledProcessError as e:
                if attempt < retries - 1:  # Not the last attempt
                    time.sleep(6.0)  # Wait 6 seconds before retry
                    continue
                self.last_error = f"OpenTabletDriver command failed: {e.stderr.strip() if e.stderr else 'Unknown error'}"
                
            except FileNotFoundError:
                self.last_error = "OpenTabletDriver not found - is it installed?"
                break  # Don't retry for this error
                
            except Exception as e:
                if attempt < retries - 1:  # Not the last attempt
                    time.sleep(3.0)  # Wait 3 seconds before retry
                    continue
                self.last_error = f"Unexpected error: {str(e)}"
        
        return None
    
    def list_presets(self) -> List[str]:
        """List all available presets"""
        if not self.presets_dir.exists():
            return []
        
        presets = []
        for file in self.presets_dir.glob("*.json"):
            presets.append(file.stem)
        
        return sorted(presets)
    
    def get_current_settings(self, for_waybar: bool = False) -> Optional[TabletSettings]:
        """Get current tablet settings with caching - only call otd getallsettings once"""
        # If we've already fetched settings this run, return cached result
        if self._settings_fetched:
            return self._cached_settings
        
        # Mark that we've attempted to fetch settings
        self._settings_fetched = True
        
        # Call otd getallsettings with retry logic (this is done in _run_otd_command)
        output = self._run_otd_command(['getallsettings'])
        
        if output is not None:
            try:
                settings = TabletSettings(output)
                # Validate that we got meaningful data
                if settings.tablet_name and settings.output_mode:
                    self._cached_settings = settings
                    return settings
                else:
                    self.last_error = "OpenTabletDriver returned incomplete tablet information"
            except Exception as e:
                self.last_error = f"Failed to parse tablet settings: {str(e)}"
                print(f"Error parsing OTD output: {e}", file=sys.stderr)
        
        # Failed to get valid settings
        self._cached_settings = None
        return None
    
    def apply_preset(self, preset_name: str) -> bool:
        """Apply a preset by name"""
        result = self._run_otd_command(['applypreset', preset_name])
        # Command succeeded if it didn't return None (empty string is OK)
        return result is not None
    
    def cycle_to_next_preset(self) -> Optional[str]:
        """Cycle to the next preset"""
        presets = self.list_presets()
        if not presets:
            return None
        
        current_settings = self.get_current_settings()
        if not current_settings:
            return None
        
        current_preset = self.matcher.find_matching_preset(current_settings, presets)
        
        try:
            current_index = presets.index(current_preset)
        except ValueError:
            current_index = -1
        
        next_index = (current_index + 1) % len(presets)
        next_preset = presets[next_index]
        
        if self.apply_preset(next_preset):
            return next_preset
        return None
    
    def cycle_to_previous_preset(self) -> Optional[str]:
        """Cycle to the previous preset"""
        presets = self.list_presets()
        if not presets:
            return None
        
        current_settings = self.get_current_settings()
        if not current_settings:
            return None
        
        current_preset = self.matcher.find_matching_preset(current_settings, presets)
        
        try:
            current_index = presets.index(current_preset)
        except ValueError:
            current_index = 0
        
        prev_index = (current_index - 1) % len(presets)
        prev_preset = presets[prev_index]
        
        if self.apply_preset(prev_preset):
            return prev_preset
        return None


def main():
    """Main function with argparse"""
    parser = argparse.ArgumentParser(
        description="OpenTabletDriver Preset Switcher for Waybar",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--list-presets', action='store_true',
                       help='List all available presets')
    parser.add_argument('--next', action='store_true',
                       help='Switch to next preset')
    parser.add_argument('--prev', '--previous', action='store_true',
                       help='Switch to previous preset')
    parser.add_argument('--waybar', action='store_true',
                       help='Output JSON format for Waybar')
    
    args = parser.parse_args()
    
    switcher = OpenTabletDriverPresetSwitcher()
    
    if args.list_presets:
        presets = switcher.list_presets()
        if presets:
            print("Available presets:")
            for i, preset in enumerate(presets):
                print(f"  {i+1}. {preset}")
        else:
            print("No presets found")
    
    elif args.next:
        next_preset = switcher.cycle_to_next_preset()
        if next_preset:
            print(f"Switched to preset: {next_preset}")
        else:
            print("Failed to switch to next preset")
    
    elif args.prev:
        prev_preset = switcher.cycle_to_previous_preset()
        if prev_preset:
            print(f"Switched to preset: {prev_preset}")
        else:
            print("Failed to switch to previous preset")
    
    elif args.waybar:
        # Output Waybar JSON format
        output = switcher.waybar_formatter.get_waybar_output()
        print(json.dumps(output))
    
    else:
        # Default: show current status (human-readable)
        current_settings = switcher.get_current_settings()
        if current_settings:
            presets = switcher.list_presets()
            current_preset = switcher.matcher.find_matching_preset(current_settings, presets)
            print(f"Tablet: {current_settings.tablet_name}")
            print(f"Current Preset: {current_preset}")
            print(f"Output Mode: {current_settings.output_mode}")
        else:
            print("Failed to get current settings")


if __name__ == "__main__":
    main()
