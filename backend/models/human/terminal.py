"""Read one line from the terminal, and return early when the phone cancels the turn."""
import sys
import threading


def read_line(cancel: threading.Event) -> str | None:
    print("模型输出> ", end="", flush=True)
    if sys.platform != "win32":
        if cancel.is_set():
            print()
            return None
        return input().strip()
    import msvcrt

    chars: list[str] = []
    while True:
        if cancel.is_set():
            print()
            return None
        if not msvcrt.kbhit():
            cancel.wait(0.05)
            continue
        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            msvcrt.getwch()
            continue
        if char == "\x03":
            print()
            raise KeyboardInterrupt
        if char in ("\r", "\n"):
            print()
            return "".join(chars).strip()
        if char in ("\b", "\x7f"):
            if chars:
                chars.pop()
                print("\b \b", end="", flush=True)
            continue
        chars.append(char)
        print(char, end="", flush=True)
