import argparse
import colorsys
import ctypes
import difflib
import glob
import json
import logging
import os
import re
import sys
from datetime import datetime

import cv2
import numpy as np
import pyautogui
import pytesseract
from PIL import Image, ImageDraw
from requests import get

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import keyboard
    import pydirectinput
    import pygetwindow
    import win32api
    import win32con
    import win32gui
    import win32ui

    pydirectinput.FAILSAFE = False
pyautogui.FAILSAFE = False


__version__ = "vDEV"

SLEEP_MULT = 1
DEBUG = False
TARGET_WINDOW = "MadokaExedra"
MOCK_IMAGE = None
text_locations = {}
result = {}
RESULT_FILE = "my_crys.json"


def take_debug_screencap(title: str | None = None):
    if not DEBUG:
        return
    if title is None:
        title = "full_screencap"
    client_left = text_locations["screen"][0]
    client_top = text_locations["screen"][1]
    img = grab_region(text_locations["screen"])
    draw = ImageDraw.Draw(img)
    for name, coords in text_locations.items():
        if len(coords) == 4:
            x1, y1, x2, y2 = coords
            x1 -= client_left
            x2 -= client_left
            y1 -= client_top
            y2 -= client_top
            x = (x1 + x2) // 2
            y = (y1 + y2) // 2
            colour = "magenta"
            draw.rectangle((x1, y1, x2, y2), outline=colour, width=5)
        else:
            x, y = coords
            x -= client_left
            y -= client_top
            colour = "red"
            r = 8
            draw.ellipse((x - r, y - r, x + r, y + r), outline=colour, width=10)

        draw.text((x + 4, y + 4), name, fill=colour)
    img.save(f"debug/{title}.png")


if IS_WINDOWS:
    keyboard.add_hotkey("ctrl+shift+q", lambda: os._exit(0))
    keyboard.add_hotkey("ctrl+shift+p", take_debug_screencap)

log_formatter = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("crys_reader")

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)


def check_git_version_match():
    try:
        git_version = get(
            "https://api.github.com/repos/thefrozenfishy/exedra-crys-reader/releases/latest",
            timeout=10,
        )
        if git_version.status_code == 200:
            data = git_version.json()
            version = data["tag_name"].lstrip("version-")
            if f"v{version}" != __version__:
                logger.warning(
                    "New version available: v%s, you are on %s", version, __version__
                )
                logger.warning(
                    "Get it on https://github.com/thefrozenfishy/exedra-crys-reader/releases/tag/version-%s",
                    version,
                )
    except Exception as e:
        logger.error("Failed to get git version")


def get_game_window():
    wins = pygetwindow.getWindowsWithTitle(TARGET_WINDOW)
    if not wins:
        raise RuntimeError("Game window not found")
    return wins[0]


def _capture_client(hwnd: int) -> Image.Image:
    win_left, win_top, win_right, win_bottom = win32gui.GetWindowRect(hwnd)
    w = win_right - win_left
    h = win_bottom - win_top

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()

    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0x2)

    bmp_info = bmp.GetInfo()
    raw = bmp.GetBitmapBits(True)
    full_img = Image.frombuffer(
        "RGB",
        (bmp_info["bmWidth"], bmp_info["bmHeight"]),
        raw,
        "raw",
        "BGRX",
        0,
        1,
    )

    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
    client_rect = win32gui.GetClientRect(hwnd)
    cx = client_left - win_left
    cy = client_top - win_top
    cw = client_rect[2]
    ch = client_rect[3]
    return full_img.crop((cx, cy, cx + cw, cy + ch))


_game_hwnd: int = 0
_client_left: int = 0
_client_top: int = 0


def grab_region(bbox) -> Image.Image:
    x1, y1, x2, y2 = bbox

    if MOCK_IMAGE:
        return MOCK_IMAGE.crop((x1, y1, x2, y2))

    img = _capture_client(_game_hwnd)
    ox, oy = _client_left, _client_top
    return img.crop((x1 - ox, y1 - oy, x2 - ox, y2 - oy))


def get_dpi_scale() -> float:
    """Return the DPI scale factor (e.g. 1.0, 1.25, 1.5)."""
    if not IS_WINDOWS:
        return 1.0
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return dpi / 96.0
    except Exception:
        return 1.0


def scroll_up(length=5):
    scroll(-length, *text_locations["scroll_location"])


def scroll_down(length=5):
    scroll(length, *text_locations["scroll_location"])


def scroll(clicks: int, x: int, y: int):
    if MOCK_IMAGE:
        return
    hwnd = win32gui.FindWindow(None, TARGET_WINDOW)
    if not hwnd:
        return
    prev_hwnd = win32gui.GetForegroundWindow()
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    pyautogui.sleep(SLEEP_MULT * 0.02)
    curr = pyautogui.position()
    pydirectinput.click(x, y)

    adjusted_delta = int(-120 / DPI_SCALE)
    if clicks < 0:
        adjusted_delta *= -1
        clicks *= -1
    for _ in range(clicks):
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, adjusted_delta, 0)
        pyautogui.sleep(SLEEP_MULT * 0.1)

    pyautogui.moveTo(curr)
    pyautogui.sleep(SLEEP_MULT * 0.02)
    ctypes.windll.user32.SetForegroundWindow(prev_hwnd)


def click_name(name):
    pyautogui.sleep(SLEEP_MULT * 1)
    click(*text_locations[name])


def click(x, y):
    if not IS_WINDOWS:
        take_debug_screencap()
        return
    hwnd = win32gui.FindWindow(None, TARGET_WINDOW)
    if not hwnd:
        logger.error("Could not find hwnd")
        return

    prev_hwnd = win32gui.GetForegroundWindow()
    ctypes.set_last_error(0)
    result = ctypes.windll.user32.SetForegroundWindow(hwnd)
    err = ctypes.get_last_error()
    if err:
        logger.warning("SetForegroundWindow result=%s err=%s", result, err)
    pyautogui.sleep(SLEEP_MULT * 0.02)
    curr = pyautogui.position()
    pydirectinput.click(int(x), int(y))
    pyautogui.moveTo(curr)
    pyautogui.sleep(SLEEP_MULT * 0.02)
    if prev_hwnd and win32gui.IsWindow(prev_hwnd):
        ctypes.windll.user32.SetForegroundWindow(prev_hwnd)
    else:
        logger.warning(
            "Previous hwnd %s is not valid, cannot restore foreground", prev_hwnd
        )


def normalize(text: str) -> str:
    return re.sub(r"\\s+", "", text).lower()


def resource_path(relative):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative)
    return os.path.abspath(relative)


def capture_client(hwnd: int) -> Image.Image:
    if MOCK_IMAGE:
        return MOCK_IMAGE.copy()
    wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
    w = wr - wl
    h = wb - wt

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()

    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)

    ctypes.windll.user32.PrintWindow(
        hwnd,
        save_dc.GetSafeHdc(),
        0x2,
    )

    bmp_info = bmp.GetInfo()
    raw = bmp.GetBitmapBits(True)

    full = Image.frombuffer(
        "RGB",
        (bmp_info["bmWidth"], bmp_info["bmHeight"]),
        raw,
        "raw",
        "BGRX",
        0,
        1,
    )

    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    cl, ct = win32gui.ClientToScreen(hwnd, (0, 0))
    cr = win32gui.GetClientRect(hwnd)

    ox = cl - wl
    oy = ct - wt

    return full.crop((ox, oy, ox + cr[2], oy + cr[3]))


def prepare_variants(img):
    arr = np.array(img)

    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(
        gray,
        None,
        fx=2,
        fy=2,
        interpolation=cv2.INTER_LINEAR,
    )

    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return [
        (bw, "bw"),
        (gray, "gray"),
    ]


def ocr_box(name):
    img = grab_region(text_locations[name])
    best = ""
    for variant, vname in prepare_variants(img):
        txt = pytesseract.image_to_string(variant, config="--oem 3 --psm 6")
        txt = re.sub(r"\\s+", " ", txt).strip()
        if len(txt) > len(best):
            best = txt
        if DEBUG:
            Image.fromarray(variant).save(f"debug/{name}_{vname}.png")
    return best


def fuzzy_match(text, names):
    norm = normalize(text)
    normalised = {normalize(x): x for x in names}
    for n, canonical in normalised.items():
        if n in norm:
            return canonical

    match = difflib.get_close_matches(text, names, n=1, cutoff=0.65)
    if match:
        return match[0]

    logger.debug("Could not read %s", text)
    return None


def is_colour_around_button_purple(name: str, radius=30) -> tuple[bool, bool]:
    mid_x, mid_y = text_locations[name]
    colour_img = grab_region(
        (
            mid_x - radius,
            mid_y - radius,
            mid_x + radius,
            mid_y + radius,
        )
    )
    arr = np.array(colour_img).astype(float) / 255.0
    r, g, b, *_ = arr.mean(axis=(0, 1))  # [R, G, B] normalized
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if DEBUG:
        os.makedirs("debug/color_radius", exist_ok=True)
        colour_img.save(
            f"debug/color_radius/{name}_{h:.2f}_{s:.2f}_{v:.2f}__{r:.2f}_{g:.2f}_{b:.2f}.png"
        )

    is_purple = h > 0.75
    is_muted = s < 0.50
    logger.debug(
        "HSV> %.2f, %.2f, %.2f, %.2f, %.2f, %.2f gave %s & %s",
        h,
        s,
        v,
        r,
        g,
        b,
        is_purple,
        is_muted,
    )
    return is_purple, is_muted


def read_sub_crys(is_currently_equipped: bool):
    pos_text = "equipped" if is_currently_equipped else "unequipped"
    iters = 5 if is_currently_equipped else 1
    c0 = None
    i = 0
    for i in range(iters):
        c0 = fuzzy_match(ocr_box(f"subcrys_name_0_{pos_text}_{i}"), sub_crys_names)
        if c0 is not None:
            break
    else:
        return []
    c1 = fuzzy_match(ocr_box(f"subcrys_name_1_{pos_text}_{i}"), sub_crys_names)
    c2 = fuzzy_match(ocr_box(f"subcrys_name_2_{pos_text}_{i}"), sub_crys_names)
    return [x for x in [c0, c1, c2] if x is not None]


def scan_all_unequipped_crys(has_crys_equipped: bool):
    x = y = 0
    crys = {}
    break_next = False
    while True:
        crys_pos = f"crys_nr_{x}_{y}"
        is_purple, is_muted = is_colour_around_button_purple(crys_pos)
        if is_purple:
            break
        eq_idx = 0
        while is_muted and eq_idx < 3:
            click_name(f"equipped_crys_{eq_idx}_pos")
            is_purple, is_muted = is_colour_around_button_purple(crys_pos)
            eq_idx += 1
        click_name(crys_pos)
        scroll_down()
        for i in range(4 if has_crys_equipped else 0, -1, -1):
            name = fuzzy_match(
                ocr_box(f"crys_name_{'un' if has_crys_equipped else ''}equipped_{i}"),
                crys_names,
            )
            if name is not None:
                break
        else:
            name = None
        if name is not None and name not in crys:
            crys[name] = read_sub_crys(not has_crys_equipped)

        x = (x + 1) % 4
        y = (y + (x == 0)) % 4
        if x == 0 and y == 0:
            if break_next:
                break
            scroll(10, *text_locations["crys_list_scroll"])
            break_next = True
        if eq_idx != 0:
            # Reset to top of we click down so that we dont shuffle equips around forever
            click_name(f"equipped_crys_0_pos")
    return crys


CRYS_FILE_PATTERN = re.compile(r"^my_crys(?:_(\d+))?\.json$")


def find_crys_files():
    found = []
    for path in glob.glob("my_crys*.json"):
        match = CRYS_FILE_PATTERN.match(os.path.basename(path))
        if not match:
            continue
        number = int(match.group(1)) if match.group(1) else 1
        found.append((number, path))
    found.sort(key=lambda pair: pair[0])
    return found


def next_new_filename(existing):
    if not existing:
        return "my_crys.json"
    highest = max(number for number, _ in existing)
    return f"my_crys_{highest + 1}.json"


def choose_result_file():
    existing = find_crys_files()

    if not existing:
        filename = "my_crys.json"
        logger.info("No old file found, creating new %s", filename)
        return filename, {}

    new_filename = next_new_filename(existing)

    print("""Found existing crys file(s). 
    If you select an old file you will add new characters to that list, while skipping characters already added.
    Which one do you want to use?""")
    print(f"  1. New file (will be saved as {new_filename})")
    for i, (_, path) in enumerate(existing, start=2):
        print(f"  {i}. {path}")

    max_choice = len(existing) + 1
    while True:
        raw = input(f"Enter a number 1-{max_choice} [default 1]: ").strip()
        if raw == "":
            choice = 1
            break
        if raw.isdigit() and 1 <= int(raw) <= max_choice:
            choice = int(raw)
            break
        print(f"Please enter a number between 1 and {max_choice}, or just press enter.")

    if choice == 1:
        logger.info("Creating new file %s", new_filename)
        return new_filename, {}

    filename = existing[choice - 2][1]
    try:
        with open(filename, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("Root of %s is not an object" % filename)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Could not parse %s, starting with an empty result", filename)
        loaded = {}

    logger.info(
        "Adding to existing file %s with %d existing entries", filename, len(loaded)
    )
    return filename, loaded


def save_result():
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, sort_keys=True)


seen_this_run = set()


def scan_all_kioku():
    click_name("crys_tab")
    scroll_up(20)
    while True:
        kioku_name = fuzzy_match(ocr_box("kioku_name"), style_names)
        logger.debug("Reading %s", kioku_name)
        if kioku_name is None:
            raise AttributeError(f"Could not read kioku name {ocr_box("kioku_name")}")
        if kioku_name in seen_this_run:
            logger.info("Came back to %s, terminating", kioku_name)
            return
        seen_this_run.add(kioku_name)
        if kioku_name in result:
            logger.info("%s already exists in %s, skipping", kioku_name, RESULT_FILE)
        else:
            has_crys_equipped = (
                fuzzy_match(ocr_box("topside_crys_0_name"), crys_names) is not None
            )
            click_name("crys_set_button")
            pyautogui.sleep(1 * SLEEP_MULT)
            result[kioku_name] = scan_all_unequipped_crys(has_crys_equipped)

            equip_order = []
            if has_crys_equipped:
                scroll_up()
                for i in range(3):
                    crys_pos = f"equipped_crys_{i}_pos"
                    click_name(crys_pos)
                    is_purple, _ = is_colour_around_button_purple(
                        "equipped_icon", radius=15
                    )
                    eq_name = None
                    if not is_purple:
                        crys_name_equipped = fuzzy_match(
                            ocr_box("crys_name_equipped_0"), crys_names
                        )
                        if crys_name_equipped is not None:
                            eq_name = crys_name_equipped
                            result[kioku_name][crys_name_equipped] = read_sub_crys(True)
                    equip_order.append(eq_name)
            click_name("crys_return_button")
            click_name("cancel_save_button")
            logger.info(
                "For %s found %d crys, where %d have substats rolled",
                kioku_name,
                len(result[kioku_name]),
                sum(
                    1 if x is not None and len(x) else 0
                    for x in result[kioku_name].values()
                ),
            )
            result[kioku_name]["meta"] = {"equipOrder": equip_order}
            save_result()
        click_name("next_kioku_button")
        pyautogui.sleep(1 * SLEEP_MULT)


def setup_text_locations_mock():
    global _client_left, _client_top
    if not MOCK_IMAGE:
        return
    w, h = MOCK_IMAGE.size

    _client_left = 0
    _client_top = 0
    text_locations["screen"] = (0, 0, w, h)
    logger.debug("Mock resolution %dx%d", w, h)

    make_text_locations(0, 0, w, h)


def setup_text_locations():
    global _game_hwnd, _client_left, _client_top
    if MOCK_IMAGE:
        setup_text_locations_mock()
        return
    if not IS_WINDOWS:
        raise RuntimeError("Window mode only supported on Windows.")

    win = get_game_window()
    hwnd = win._hWnd
    client_rect = win32gui.GetClientRect(hwnd)
    left_top = win32gui.ClientToScreen(hwnd, (0, 0))
    right_bottom = win32gui.ClientToScreen(hwnd, (client_rect[2], client_rect[3]))
    client_left, client_top = left_top
    client_right, client_bottom = right_bottom

    client_width = client_right - client_left
    client_height = client_bottom - client_top

    logger.debug("Client area resolution is %dx%d", client_width, client_height)

    _game_hwnd = hwnd
    _client_left = client_left
    _client_top = client_top
    make_text_locations(client_left, client_top, client_width, client_height)


def make_text_locations(client_left, client_top, client_width, client_height):
    text_locations["kioku_name"] = (
        int(client_left + 0.61 * client_width),
        int(client_top + 0.16 * client_height),
        int(client_left + 0.95 * client_width),
        int(client_top + 0.21 * client_height),
    )
    text_locations["crys_name_equipped_0"] = (
        int(client_left + 0.59 * client_width),
        int(client_top + 0.18 * client_height),
        int(client_left + 0.92 * client_width),
        int(client_top + 0.23 * client_height),
    )
    for i in range(5):
        text_locations[f"crys_name_unequipped_{i}"] = (
            int(client_left + 0.59 * client_width),
            int(client_top + (0.34 + 0.04 * i) * client_height),
            int(client_left + 0.92 * client_width),
            int(client_top + (0.42 + 0.04 * i) * client_height),
        )
    text_locations["crys_set_button"] = (
        int(client_left + 0.93 * client_width),
        int(client_top + 0.43 * client_height),
    )
    text_locations["crys_tab"] = (
        int(client_left + 0.90 * client_width),
        int(client_top + 0.30 * client_height),
    )
    text_locations["scroll_location"] = (
        int(client_left + 0.8 * client_width),
        int(client_top + 0.33 * client_height),
    )
    text_locations["crys_list_scroll"] = (
        int(client_left + 0.5 * client_width),
        int(client_top + 0.33 * client_height),
    )
    text_locations["cancel_save_button"] = (
        int(client_left + 0.6 * client_width),
        int(client_top + 0.8 * client_height),
    )
    text_locations["next_kioku_button"] = (
        int(client_left + 0.56 * client_width),
        int(client_top + 0.5 * client_height),
    )
    text_locations["crys_return_button"] = (
        int(client_left + 0.96 * client_width),
        int(client_top + 0.05 * client_height),
    )
    for x in range(4):
        for y in range(4):
            text_locations[f"crys_nr_{x}_{y}"] = (
                int(client_left + (0.163 + 0.094 * x) * client_width),
                int(client_top + (0.29 + 0.168 * y) * client_height),
            )

    for i in range(5):
        text_locations[f"subcrys_name_0_equipped_{i}"] = (
            int(client_left + 0.54 * client_width),
            int(client_top + (0.34 + 0.04 * i) * client_height),
            int(client_left + 0.75 * client_width),
            int(client_top + (0.40 + 0.04 * i) * client_height),
        )
        text_locations[f"subcrys_name_1_equipped_{i}"] = (
            int(client_left + 0.76 * client_width),
            int(client_top + (0.34 + 0.04 * i) * client_height),
            int(client_left + 0.97 * client_width),
            int(client_top + (0.40 + 0.04 * i) * client_height),
        )
        text_locations[f"subcrys_name_2_equipped_{i}"] = (
            int(client_left + 0.54 * client_width),
            int(client_top + (0.40 + 0.04 * i) * client_height),
            int(client_left + 0.75 * client_width),
            int(client_top + (0.46 + 0.04 * i) * client_height),
        )

    text_locations["subcrys_name_0_unequipped_0"] = (
        int(client_left + 0.54 * client_width),
        int(client_top + 0.69 * client_height),
        int(client_left + 0.75 * client_width),
        int(client_top + 0.75 * client_height),
    )
    text_locations["subcrys_name_1_unequipped_0"] = (
        int(client_left + 0.76 * client_width),
        int(client_top + 0.69 * client_height),
        int(client_left + 0.97 * client_width),
        int(client_top + 0.75 * client_height),
    )
    text_locations["subcrys_name_2_unequipped_0"] = (
        int(client_left + 0.54 * client_width),
        int(client_top + 0.75 * client_height),
        int(client_left + 0.75 * client_width),
        int(client_top + 0.81 * client_height),
    )
    for i in range(3):
        text_locations[f"equipped_crys_{i}_pos"] = (
            int(client_left + 0.05 * client_width),
            int(client_top + (0.35 + 0.13 * i) * client_height),
        )
    text_locations["topside_crys_0_name"] = (
        int(client_left + 0.68 * client_width),
        int(client_top + 0.42 * client_height),
        int(client_left + 0.88 * client_width),
        int(client_top + 0.48 * client_height),
    )
    text_locations["equipped_icon"] = (
        int(client_left + 0.56 * client_width),
        int(client_top + 0.185 * client_height),
    )
    text_locations["screen"] = (
        client_left,
        client_top,
        client_left + client_width,
        client_top + client_height,
    )

    take_debug_screencap()


def main():
    global RESULT_FILE, result
    logger.info(
        """Reading all crys and subcrys, let the game be until this terminates naturally.
    There are potentially hundreds or thousands of crys based on your active filter,
    so this can take a long time (ca 4 sec per crys / up to 2 min per kioku)."""
    )
    logger.info(
        "Press Ctrl+Shift+Q to terminate the program prematurely, kioku already analyzed will be saved to file."
    )
    logger.debug("Current version %s", __version__)
    check_git_version_match()
    setup_text_locations()
    RESULT_FILE, result = choose_result_file()
    try:
        scan_all_kioku()
    except Exception:
        logger.exception("An issue occured")
        input(
            f"Press enter to close, all crys that was discovered will be written to {RESULT_FILE}"
        )
    finally:
        save_result()


with open(resource_path("getStyleMstList.json"), encoding="utf8") as f:
    style_names = [s["name"] for s in json.load(f)["payload"]["mstList"]]

with open(resource_path("getSelectionAbilityMstList.json"), encoding="utf8") as f:
    data = json.load(f)["payload"]["mstList"]
    crys_names = {
        s["name"] for s in data if s["selectionAbilityType"] == 1 and s["rarity"] > 2
    }
    sub_crys_names = {s["name"] for s in data if s["selectionAbilityType"] == 2}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--target")
    parser.add_argument("--mock-image")

    args = parser.parse_args()

    custom_target = args.target
    if custom_target:
        logger.info("Using custom game name '%s'", custom_target)
        TARGET_WINDOW = custom_target

    if args.mock_image:
        MOCK_IMAGE = Image.open(args.mock_image)

    DEBUG = args.debug
    logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
    if DEBUG:
        os.makedirs("debug/logs", exist_ok=True)
        file_handler = logging.FileHandler(
            f"debug/logs/{datetime.today().strftime('%Y-%m-%dT%H-%M-%S')}.txt",
            encoding="utf-8",
        )
        file_handler.setFormatter(log_formatter)
        logger.addHandler(file_handler)

    DPI_SCALE = get_dpi_scale()
    logger.debug("DPI scale factor detected: %.2f", DPI_SCALE)

    if not IS_WINDOWS and not MOCK_IMAGE:
        raise RuntimeError(
            "On macOS/Linux you must provide " "--mock-image screenshot.png"
        )

    main()
