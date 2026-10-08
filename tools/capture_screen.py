import ctypes
from ctypes import wintypes
from PIL import Image
import os, sys

def capture(out_path="runtime/captures/live_battle_latest.png"):
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    hwnd = user32.FindWindowW(None, '口袋妖怪黑2 [NDS] - BizHawk (interim)')
    if not hwnd:
        def enum_cb(h, extra):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(h, buf, 256)
            if 'BizHawk' in buf.value or '口袋妖怪' in buf.value:
                extra.append(h)
            return True
        matched = []
        cb = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, ctypes.c_void_p)(lambda h, l: enum_cb(h, matched))
        user32.EnumWindows(cb, 0)
        if matched:
            hwnd = matched[0]
        else:
            print('BizHawk window not found')
            return None

    class RECT(ctypes.Structure):
        _fields_ = [
            ('left', wintypes.LONG),
            ('top', wintypes.LONG),
            ('right', wintypes.LONG),
            ('bottom', wintypes.LONG),
        ]

    rc = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    w = rc.right - rc.left
    h = rc.bottom - rc.top

    hdc_window = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    hbm = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
    gdi32.SelectObject(hdc_mem, hbm)

    user32.PrintWindow(hwnd, hdc_mem, 2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ('biSize', wintypes.DWORD),
            ('biWidth', wintypes.LONG),
            ('biHeight', wintypes.LONG),
            ('biPlanes', wintypes.WORD),
            ('biBitCount', wintypes.WORD),
            ('biCompression', wintypes.DWORD),
            ('biSizeImage', wintypes.DWORD),
            ('biXPelsPerMeter', wintypes.LONG),
            ('biYPelsPerMeter', wintypes.LONG),
            ('biClrUsed', wintypes.DWORD),
            ('biClrImportant', wintypes.DWORD),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    buf = bytearray(w * h * 4)
    c_buf = (ctypes.c_char * len(buf)).from_buffer(buf)

    gdi32.GetDIBits(hdc_mem, hbm, 0, h, c_buf, ctypes.byref(bmi), 0)

    gdi32.DeleteObject(hbm)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)

    img = Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    print(f'Captured to {out_path}')
    return out_path

if __name__ == '__main__':
    p = sys.argv[1] if len(sys.argv) > 1 else 'runtime/captures/live_battle_latest.png'
    capture(p)
