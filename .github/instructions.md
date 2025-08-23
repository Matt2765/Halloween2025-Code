# Instructions for Halloween2025-Code

## Project Overview
This codebase controls a multi-room haunted house automation system for Halloween events. It orchestrates audio, lighting, sensors, and hardware (e.g., Arduino, ESP32) across themed rooms. The system is modular, with clear separation between control logic, room behaviors, UI, and hardware integration.

## Key Components
- `HauntedHouse2025/control/`: Core logic for hardware (Arduino, sensors), audio, lighting, and system state.
- `HauntedHouse2025/rooms/`: Room-specific logic (e.g., `cave.py`, `swamp.py`). Each room imports shared context and audio control.
- `HauntedHouse2025/ui/`: User interfaces, including a Tkinter GUI (`gui.py`) and HTTP server (`http_server.py`).
- `HauntedHouse2025/utils/`: Utility functions for logging, timing, and debugging.
- `HauntedHouse2025/context.py`: Central state and configuration shared across modules.
- `HauntedHouse2025/main.py`: Likely the main entry point for running the system (verify actual entrypoint).
- `[OLD]HalloweenFramework2025 v1.py`: Legacy monolithic version; reference for migration or historical logic.

## Patterns & Conventions
- **Room modules**: Import `context.house` and `control.audio_manager.play_to_named_channel` for state and sound.
- **Hardware abstraction**: All Arduino and sensor logic is in `control/`. Use provided functions (e.g., `connectArduino`, `setDoorState`)—do not access hardware directly from rooms/UI.
- **Logging**: Use `utils.tools.log_event` for event logging. Log file: `logs/haunt_log.txt`.
- **Threading/Processes**: System uses threads and processes for parallel tasks (audio, HTTP server, GUI, etc.).
- **State management**: Shared state is in `context.py` and `house_state.py`.

## Developer Workflows
- **Run the system**: Start from `main.py` in `HauntedHouse2025/` (or check for a launcher script).
- **Testing**: No standard test suite detected; manual testing via UI and hardware is likely.
- **Debugging**: Use `utils/debug.py` and log files. Many modules have debug helpers.
- **Adding a room**: Copy an existing room module, import `context.house`, and register with the system.
- **Hardware integration**: Add new hardware logic in `control/`, not in rooms or UI.

## Integration Points
- **Arduino**: Controlled via `control/arduino.py` (uses pymata4).
- **ESP32**: Code in `Esp32/` (not Python); communicates with main system via sensors/network.
- **Audio**: Managed by `control/audio_manager.py` and `pydub`.
- **UI**: Tkinter GUI and HTTP server run in parallel threads.

## Examples
- To trigger a sound in a room: `play_to_named_channel('cave', 'spooky.wav')`
- To log an event: `log_event('Door opened')`
- To toggle demo mode: `toggle_demo_mode(state, enable=True)`

## Special Notes
- Do not modify `[OLD]HalloweenFramework2025 v1.py` unless porting legacy logic.
- All cross-component communication should go through shared context or control modules.
- Use relative imports within `HauntedHouse2025/`.

---
_Revise this file as the project evolves. For unclear areas, consult the legacy file or ask maintainers._
