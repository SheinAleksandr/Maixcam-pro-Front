# MaixCAM Pro / MaixPy
# Счетчик ковшей с AprilTag (640x480)

from maix import image, camera, display, time
from maix.touchscreen import TouchScreen

# =========================
# Настройки видео
# =========================
W, H = 640, 480
cam = camera.Camera(W, H)
disp = display.Display()

# =========================
# Сенсорный экран
# =========================
try:
    ts = TouchScreen()
    touch_ok = True
except Exception as e:
    ts = None
    touch_ok = False

# =========================
# Сохранение LINE_Y в файл
# =========================
CFG_PATH = "/root/liney.cfg"

def load_line_y(default_y: int) -> int:
    try:
        with open(CFG_PATH, "r") as f:
            return int(f.read().strip())
    except Exception:
        return default_y

def save_line_y(v: int) -> None:
    try:
        with open(CFG_PATH, "w") as f:
            f.write(str(int(v)))
    except Exception:
        pass

# =========================
# Настройки AprilTag
# =========================
FAMILY = image.ApriltagFamilies.TAG36H11
TRACK_ID = 0  # ИЗМЕНИТЕ на ID вашего тега

# =========================
# ROI (область поиска тега)
# =========================
ROI_X, ROI_Y, ROI_W, ROI_H = 80, 0, 480, 260

# =========================
# Настройки линии подсчета
# =========================
LINE_MIN = ROI_Y + 10
LINE_MAX = ROI_Y + ROI_H - 10
LINE_Y_DEFAULT = ROI_Y + int(ROI_H * 0.80)
LINE_Y = load_line_y(LINE_Y_DEFAULT)
LINE_Y = max(LINE_MIN, min(LINE_Y, LINE_MAX))

# =========================
# Конечный автомат подсчета
# =========================
CONFIRM_FRAMES = 5  # кадров для подтверждения
COOLDOWN_MS = 2000  # время между подсчетами

state = "BELOW"       # состояния: BELOW -> ABOVE_CHECK -> WAIT_RETURN
above_streak = 0      # счетчик кадров выше линии
last_count_ms = 0     # время последнего подсчета
bucket_count = 0      # общее количество ковшей

# =========================
# Кнопки интерфейса
# =========================
# Кнопка LINE (верхний левый угол)
BTN_LINE_X, BTN_LINE_Y, BTN_LINE_W, BTN_LINE_H = 8, 8, 140, 44

# Кнопка RESET (верхний правый угол)
BTN_RST_W, BTN_RST_H = 140, 44
BTN_RST_X, BTN_RST_Y = W - BTN_RST_W - 8, 8

# Кнопки ручной регулировки
BTN_ADJ_W, BTN_ADJ_H = 100, 60
# +1 справа снизу
BTN_PLUS_X  = W - BTN_ADJ_W - 10
BTN_PLUS_Y  = int(H * 0.75) - BTN_ADJ_H // 2
# -1 слева снизу (симметрично +1)
BTN_MINUS_X = 10
BTN_MINUS_Y = int(H * 0.75) - BTN_ADJ_H // 2

adjust_mode = False           # режим регулировки линии
reset_debounce_until = 0      # защита от дребезга RESET
touch_debounce_until = 0      # защита от дребезга касаний

# =========================
# Вспышка и масштабы текста
# =========================
FLASH_MS = 900                # длительность вспышки
flash_until_ms = 0            # время окончания вспышки
flash_value = 0               # значение для вспышки

COUNTER_SCALE = 10            # масштаб большого счетчика
FLASH_SCALE = 12              # масштаб текста вспышки

# =========================
# Статистика для отображения
# =========================
last_status = "Waiting bucket"  # статус обнаружения
fsm_status = "Ready to count"   # статус автомата

def clamp(v, lo, hi):
    """Ограничение значения в диапазоне"""
    return lo if v < lo else hi if v > hi else v

def in_rect(x, y, rx, ry, rw, rh):
    """Проверка попадания точки в прямоугольник"""
    return (x >= rx) and (x < rx + rw) and (y >= ry) and (y < ry + rh)

def est_text_w(text: str, scale: int) -> int:
    """Оценка ширины текста"""
    return len(text) * 8 * scale

def est_text_h(scale: int) -> int:
    """Оценка высоты текста"""
    return 16 * scale

def read_touch():
    """Чтение касаний сенсорного экрана"""
    if not touch_ok:
        return (False, 0, 0)
    try:
        ev = ts.read()
        if not ev:
            return (False, 0, 0)

        # Формат 1: кортеж/список (x, y, нажато)
        if isinstance(ev, (tuple, list)) and len(ev) >= 3:
            x, y, pressed = ev[0], ev[1], ev[2]
            return (bool(pressed), int(x), int(y))

        # Формат 2: объект с полями x, y
        if hasattr(ev, "x") and hasattr(ev, "y"):
            x = int(ev.x)
            y = int(ev.y)
            pressed = True
            if hasattr(ev, "pressed"):
                pressed = bool(ev.pressed)
            return (pressed, x, y)

        return (False, 0, 0)
    except Exception:
        return (False, 0, 0)

def pick_target(tags):
    """Выбор целевого тега по ID и качеству"""
    best = None
    best_score = -1.0
    for t in tags:
        try:
            if t.id() != TRACK_ID:
                continue
            dm = t.decision_margin()
            if dm < 0.18:  #                                  качество     НАСТРОИТЬ
                continue
            score = dm + 0.001 * (t.w() * t.h())
            if score > best_score:
                best_score = score
                best = t
        except Exception:
            continue
    return best

# =========================
# Основной цикл
# =========================
while True:
    img = cam.read()
    now = time.ticks_ms()

    # -------------------------
    # Обработка касаний
    # -------------------------
    pressed, tx, ty = read_touch()
    if pressed and now >= touch_debounce_until:
        touch_debounce_until = now + 180

        # Кнопка LINE - переключение режима регулировки
        if in_rect(tx, ty, BTN_LINE_X, BTN_LINE_Y, BTN_LINE_W, BTN_LINE_H):
            adjust_mode = not adjust_mode

        # Кнопка RESET - сброс счетчика
        elif in_rect(tx, ty, BTN_RST_X, BTN_RST_Y, BTN_RST_W, BTN_RST_H):
            if now >= reset_debounce_until:
                bucket_count = 0
                state = "BELOW"
                above_streak = 0
                last_count_ms = 0
                flash_until_ms = 0
                reset_debounce_until = now + 500

        # Кнопка +1 - ручное увеличение
        elif in_rect(tx, ty, BTN_PLUS_X, BTN_PLUS_Y, BTN_ADJ_W, BTN_ADJ_H):
            bucket_count += 1
            flash_value = bucket_count
            flash_until_ms = now + 500

        # Кнопка -1 - ручное уменьшение
        elif in_rect(tx, ty, BTN_MINUS_X, BTN_MINUS_Y, BTN_ADJ_W, BTN_ADJ_H):
            if bucket_count > 0:
                bucket_count -= 1
            flash_value = bucket_count
            flash_until_ms = now + 500

        # Регулировка линии касанием (в режиме регулировки)
        elif adjust_mode:
            new_y = clamp(ty, LINE_MIN, LINE_MAX)
            if new_y != LINE_Y:
                LINE_Y = new_y
                save_line_y(LINE_Y)

    # -------------------------
    # Поиск AprilTag
    # -------------------------
    tags = img.find_apriltags(families=FAMILY, roi=(ROI_X, ROI_Y, ROI_W, ROI_H))
    target = pick_target(tags)

    # -------------------------
    # Автомат подсчета
    # -------------------------
    if target:
        cy = target.cy()  # Y-координата центра тега
        
        # Обновление статуса
        last_status = f"ID: {target.id()} DM: {target.decision_margin():.2f}"
        fsm_status = "Ready to count"  # сброс статуса
        
        # Состояние BELOW - ковш ниже линии
        if state == "BELOW":
            if cy < LINE_Y:  # поднялся выше линии
                above_streak = 1
                state = "ABOVE_CHECK"
                fsm_status = "Confirming rise"

        # Состояние ABOVE_CHECK - подтверждение подъема
        elif state == "ABOVE_CHECK":
            if cy < LINE_Y:
                above_streak += 1
                if above_streak >= CONFIRM_FRAMES:
                    state = "WAIT_RETURN"
                    fsm_status = "Waiting return"
            else:
                above_streak = 0
                state = "BELOW"

        # Состояние WAIT_RETURN - ожидание возвращения
        elif state == "WAIT_RETURN":
            if cy >= LINE_Y:  # вернулся ниже линии
                if (now - last_count_ms) > COOLDOWN_MS:
                    bucket_count += 1
                    last_count_ms = now
                    flash_value = bucket_count
                    flash_until_ms = now + FLASH_MS
                state = "BELOW"
                above_streak = 0
        
        if state == "WAIT_RETURN":
            fsm_status = "Waiting return"
    else:
        last_status = "Bucket not found"
        fsm_status = "Ready to count"

    # -------------------------
    # Отрисовка интерфейса
    # -------------------------
    # Линия подсчета (желтая)
    img.draw_line(0, LINE_Y, W - 1, LINE_Y, image.COLOR_YELLOW)

    # Рамка ROI (серая, только в режиме регулировки)
    if adjust_mode:
        img.draw_rect(ROI_X, ROI_Y, ROI_W, ROI_H, image.COLOR_GRAY)

    # Отрисовка обнаруженного тега (красная рамка и крест)
    if target:
        img.draw_rect(target.x(), target.y(), target.w(), target.h(), image.COLOR_RED)
        img.draw_cross(target.cx(), target.cy(), image.COLOR_RED)

    # Кнопка LINE (синяя/зеленая)
    line_btn_color = image.COLOR_GREEN if adjust_mode else image.COLOR_BLUE
    img.draw_rect(BTN_LINE_X, BTN_LINE_Y, BTN_LINE_W, BTN_LINE_H, line_btn_color, thickness=-1)
    img.draw_string(BTN_LINE_X + 8, BTN_LINE_Y + 12, f"LINE", image.COLOR_WHITE, scale=2)

    # Кнопка RESET (красная)
    img.draw_rect(BTN_RST_X, BTN_RST_Y, BTN_RST_W, BTN_RST_H, image.COLOR_RED, thickness=-1)
    img.draw_string(BTN_RST_X + 20, BTN_RST_Y + 12, "RESET", image.COLOR_WHITE, scale=2)

    # Кнопка -1 слева (оранжевая)
    img.draw_rect(BTN_MINUS_X, BTN_MINUS_Y, BTN_ADJ_W, BTN_ADJ_H, image.COLOR_ORANGE, thickness=-1)
    img.draw_string(BTN_MINUS_X + 28, BTN_MINUS_Y + 16, "-1", image.COLOR_WHITE, scale=3)

    # Кнопка +1 справа (оранжевая)
    img.draw_rect(BTN_PLUS_X, BTN_PLUS_Y, BTN_ADJ_W, BTN_ADJ_H, image.COLOR_ORANGE, thickness=-1)
    img.draw_string(BTN_PLUS_X + 28, BTN_PLUS_Y + 16, "+1", image.COLOR_WHITE, scale=3)

    # Большой счетчик ковшей (жирный)
    counter_text = str(bucket_count)
    tw = est_text_w(counter_text, COUNTER_SCALE)
    th = est_text_h(COUNTER_SCALE)
    cx = (W - tw) // 2
    cy_text = H - th - 10
    
    # Жирная тень (4 угла)
    img.draw_string(cx + 2, cy_text + 2, counter_text, image.COLOR_BLACK, scale=COUNTER_SCALE)
    img.draw_string(cx - 2, cy_text + 2, counter_text, image.COLOR_BLACK, scale=COUNTER_SCALE)
    img.draw_string(cx + 2, cy_text - 2, counter_text, image.COLOR_BLACK, scale=COUNTER_SCALE)
    img.draw_string(cx - 2, cy_text - 2, counter_text, image.COLOR_BLACK, scale=COUNTER_SCALE)
    
    # Жирный красный контур (8 направлений)
    offsets = [(1,0), (-1,0), (0,1), (0,-1), (1,1), (-1,-1), (1,-1), (-1,1)]
    for dx, dy in offsets:
        img.draw_string(cx + dx, cy_text + dy, counter_text, image.COLOR_RED, scale=COUNTER_SCALE)
    
    # Белая сердцевина
    img.draw_string(cx, cy_text, counter_text, image.COLOR_WHITE, scale=COUNTER_SCALE)

    # Статистика внизу экрана
    img.draw_string(10, H - 40, last_status, image.COLOR_GREEN, scale=2)  # ID и DM
    img.draw_string(10, H - 70, fsm_status, image.COLOR_GREEN, scale=2)   # Статус автомата

    # Полноэкранная вспышка при подсчете
    if now < flash_until_ms:
        img.draw_rect(0, 0, W, H, image.COLOR_BLACK, thickness=-1)
        big = str(flash_value)
        btw = est_text_w(big, FLASH_SCALE)
        bth = est_text_h(FLASH_SCALE)
        bx = (W - btw) // 2
        by = (H - bth) // 2
        img.draw_string(bx + 4, by + 4, big, image.COLOR_BLACK, scale=FLASH_SCALE)
        img.draw_string(bx, by, big, image.COLOR_WHITE, scale=FLASH_SCALE)

    # Показ кадра
    disp.show(img)