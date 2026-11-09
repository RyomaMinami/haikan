"""Pure layout/text utilities, independent of Inventor and its COM wrappers."""
import math
import re
from xml.etree import ElementTree as ET


def standard_scales(maximum):
    if not math.isfinite(maximum) or maximum <= 0:
        raise ValueError('Scale must be finite and positive')
    # Preferred engineering scales: ... 1:20, 1:10, 1:5, 1:2, 1:1, 2:1 ...
    power = math.floor(math.log10(maximum))
    return sorted({m * 10.0**p for p in range(power - 12, power + 1)
                   for m in (1, 2, 5) if m * 10.0**p <= maximum * (1 + 1e-10)}, reverse=True)


def scale_label(scale):
    return f'{scale:g}:1' if scale >= 1 else f'1:{1 / scale:g}'


SLOTS = {'front': (0, 0), 'top': (0, 1), 'bottom': (0, -1),
         'left': (-1, 0), 'right': (1, 0), 'back': (2, 0), 'iso': (2, 0)}


def plan_views(sizes, maximum, area, gap=1.6):
    """Sizes at scale 1; cm. Preserve shared X/Y for third-angle parent views."""
    left, bottom, right, top = area
    slots = dict(SLOTS)
    if 'back' in sizes and 'iso' in sizes:
        slots['iso'] = (3, 0)
    for scale in standard_scales(maximum):
        widths, heights = {}, {}
        for name, (w, h) in sizes.items():
            col, row = slots[name]
            widths[col] = max(widths.get(col, 0), w * scale)
            heights[row] = max(heights.get(row, 0), h * scale)
        width = sum(widths.values()) + gap * (len(widths) - 1)
        height = sum(heights.values()) + gap * (len(heights) - 1)
        if width > right - left or height > top - bottom:
            continue
        def centers(extents, origin):
            found = {}
            for key in sorted(extents):
                found[key] = origin + extents[key] / 2
                origin += extents[key] + gap
            return found
        xs = centers(widths, left + (right - left - width) / 2)
        ys = centers(heights, bottom + (top - bottom - height) / 2)
        return scale, {name: (xs[slots[name][0]], ys[slots[name][1]]) for name in sizes}
    raise ValueError('No supported standard scale fits; reduce views or use a larger sheet')


def plain_formatted_text(formatted, fallback=''):
    """Keep paragraph boundaries which Inventor's .Text flattens. No symbol guesses."""
    try:
        root = ET.fromstring('<root>' + formatted + '</root>')
        def walk(node):
            value = node.text or ''
            if node.tag.lower() in ('br', 'break'):
                value = '\n' + value
            for child in node:
                value += walk(child) + (child.tail or '')
            return value
        return walk(root)
    except (ET.ParseError, TypeError):
        return fallback


def readable_dimension(raw, formatted, is_diameter=False):
    # AIGDT's n is the diameter glyph, not an ASCII n. Preserve both source forms.
    if re.search(r'Font\s*=\s*[\"\']AIGDT[\"\']', formatted, re.I) or (is_diameter and '<DimensionValue' in formatted):
        return re.sub(r'(?<![A-Za-z])n(?=\d)', 'φ', raw)
    return raw
