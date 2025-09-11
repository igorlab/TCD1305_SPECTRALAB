def scan_serial_ports():
    """
    Повертає список доступних послідовних портів (COM на Windows).
    Працює без зовнішніх залежностей:
    - Windows: читає з реєстру HKLM\\HARDWARE\\DEVICEMAP\\SERIALCOMM
    - Linux: перевіряє стандартні /dev/tty* шаблони
    - macOS: перевіряє лише /dev/cu.*
    """
    import sys
    import os
    import glob

    ports = []

    if sys.platform.startswith('win'):
        try:
            import winreg  # доступний у стандартній бібліотеці на Windows
            path = r'HARDWARE\DEVICEMAP\SERIALCOMM'
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                index = 0
                while True:
                    try:
                        # val = (name, data, type), де data — це "COMx"
                        val = winreg.EnumValue(key, index)
                        ports.append(str(val[1]))
                        index += 1
                    except OSError:
                        break
        except Exception:
            # Якщо не вдалося прочитати реєстр, повертаємо те, що вдалося (можливо, порожній список)
            pass

        def _com_sort_key(p):
            try:
                return int(p[3:])
            except Exception:
                return 10**9

        return sorted(set(ports), key=_com_sort_key)

    # Unix-подібні системи
    patterns = []
    if sys.platform == 'darwin':
        patterns = ['/dev/cu.*'] #  '/dev/tty.*',
    else:
        patterns = [
            '/dev/ttyUSB*',
            '/dev/ttyACM*',
            '/dev/ttyS*',
            '/dev/ttyAMA*',
            '/dev/rfcomm*',
            '/dev/serial/by-id/*',
        ]

    for pat in patterns:
        for p in glob.glob(pat):
            try:
                real = os.path.realpath(p)
            except Exception:
                real = p
            ports.append(real)

    # Унікалізація зі збереженням порядку та перевіркою існування
    seen = set()
    result = []
    for p in ports:
        # Пропускаємо системний Bluetooth Incoming Port на macOS
        if sys.platform == 'darwin': base = os.path.basename(p).lower()
         # не показувати Bluetooth-Incoming-Port та будь-які debug-консолі (містять "debug")
        if 'bluetooth-incoming-port' in base or 'debug' in base:
            continue
        if p not in seen and os.path.exists(p):
            seen.add(p)
            result.append(p)
    return result