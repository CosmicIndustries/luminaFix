#!/usr/bin/env python3
"""
LuminaFix — Display Uniformity Corrector
Detects panel hardware via EDID, profiles known issues, applies optimal corrections.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib
import cairo
import subprocess, os, struct, json, re, math, sys
from pathlib import Path

# ── Panel Knowledge Base ────────────────────────────────────────────────────────
# Scores 0.0–1.0 derived from aggregated display reviews and manufacturer QC data

MANUFACTURERS = {
    'AUO': 'AU Optronics',   'BOE': 'BOE Technology',
    'CMN': 'Chimei Innolux', 'LGD': 'LG Display',
    'SDC': 'Samsung Display', 'SHP': 'Sharp',
    'IVO': 'InfoVision',     'HSD': 'HannStar',
    'CPT': 'Chunghwa',       'NCP': 'Innolux Corp',
    'PHL': 'Philips',        'DEL': 'Dell',
    'HWP': 'HP',             'LEN': 'Lenovo',
    'ACR': 'Acer',           'ASU': 'ASUS',
    'SEC': 'Samsung',        'SNY': 'Sony',
}

PANEL_DB = {
    'AUO': dict(bleed=0.50, gradient=0.30, clouding=0.50,
                notes='Variable QC. Mid-tier IPS; budget to mid-range laptops. '
                      'Common in Asus, Acer, HP. Moderate bleed on dark content.'),
    'BOE': dict(bleed=0.75, gradient=0.55, clouding=0.70,
                notes='Notably high bleed rates — consistently flagged in reviews. '
                      'Widely used in budget/mid Chinese OEMs (Xiaomi, Lenovo IdeaPad). '
                      'Edge and corner glow significant at low brightness.'),
    'CMN': dict(bleed=0.50, gradient=0.35, clouding=0.50,
                notes='Chimei Innolux — moderate uniformity. Similar to AUO. '
                      'Found across Dell, HP, Lenovo mid-range. Corner clouding common.'),
    'LGD': dict(bleed=0.25, gradient=0.20, clouding=0.25,
                notes='LG Display — generally above average uniformity. '
                      'Premium panels used in MacBook, ThinkPad X1, Dell XPS. Low bleed.'),
    'SDC': dict(bleed=0.20, gradient=0.15, clouding=0.20,
                notes='Samsung Display — among the best in class for uniformity. '
                      'AMOLED variants have no backlight bleed by design. '
                      'IPS variants also tight QC.'),
    'SHP': dict(bleed=0.15, gradient=0.10, clouding=0.15,
                notes='Sharp — excellent uniformity. Used in premium ThinkPads, '
                      'Surface devices. Very consistent backlight distribution.'),
    'IVO': dict(bleed=0.85, gradient=0.65, clouding=0.80,
                notes='InfoVision — worst average uniformity in class. '
                      'Significant edge glow and clouding commonly reported. '
                      'Found in budget laptops. Aggressive correction recommended.'),
    'HSD': dict(bleed=0.70, gradient=0.60, clouding=0.70,
                notes='HannStar — significant bleed issues reported across units. '
                      'Older panel tech; found in budget/older machines.'),
    'CPT': dict(bleed=0.60, gradient=0.40, clouding=0.55,
                notes='Chunghwa Picture Tubes — moderate to high bleed. '
                      'Gradient uniformity issues on dark grey screens.'),
    'NCP': dict(bleed=0.50, gradient=0.35, clouding=0.50,
                notes='Innolux Corp — similar performance to Chimei Innolux (CMN). '
                      'Moderate uniformity; common in mid-range segments.'),
}

DEFAULT_PANEL = dict(bleed=0.50, gradient=0.40, clouding=0.50,
                     notes='Unknown panel — conservative defaults applied. '
                           'Adjust sliders based on visual inspection.')

TECHNIQUE_INFO = {
    'combined':       ('Combined Correction',
                       'Edge mask overlay + gamma darkening. Recommended for severe bleed (IVO, BOE, HSD). '
                       'The overlay darkens edges via Cairo compositing; gamma reduces overall bleed visibility.'),
    'edge_mask':      ('Edge Mask Overlay',
                       'Transparent Cairo layer darkens screen edges to compensate backlight bleed. '
                       'No gamma changes — purely visual correction via compositor overlay.'),
    'uniformity_mask':('Uniformity Mask',
                       'Radial gradient overlay compensates for center-bright or edge-dark panel non-uniformity. '
                       'Best for gradient banding visible on grey or near-black fills.'),
    'gamma_darken':   ('Gamma Darkening',
                       'Adjusts xrandr gamma curves to reduce perceived bleed at edges. '
                       'Lighter touch than overlay — good for mild cases.'),
    'minimal':        ('Minimal Adjustment',
                       'Panel uniformity is good. Minor gamma fine-tuning available. '
                       'No overlay correction needed.'),
}

# ── EDID Parsing ────────────────────────────────────────────────────────────────

EDID_MAGIC = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])

def decode_mfr(b8, b9):
    try:
        c1 = chr(((b8 >> 2) & 0x1F) + 64)
        c2 = chr((((b8 & 0x03) << 3) | ((b9 >> 5) & 0x07)) + 64)
        c3 = chr((b9 & 0x1F) + 64)
        code = c1 + c2 + c3
        return code if code.isalpha() else 'UNK'
    except Exception:
        return 'UNK'

def parse_edid(data: bytes) -> dict | None:
    if len(data) < 128 or data[:8] != EDID_MAGIC:
        return None

    mfr = decode_mfr(data[8], data[9])
    product_code = struct.unpack_from('<H', data, 10)[0]

    # Preferred detailed timing (offset 54)
    h_active = ((data[58] >> 4) << 8) | data[56]
    v_active = ((data[61] >> 4) << 8) | data[59]
    h_mm     = (((data[68] >> 4) & 0x0F) << 8) | data[66]
    v_mm     =  ((data[68] & 0x0F) << 8) | data[67]

    diag_in = 0.0
    if h_mm > 0 and v_mm > 0:
        diag_in = round(math.sqrt(h_mm**2 + v_mm**2) / 25.4, 1)

    digital = bool(data[24] & 0x80)
    iface = 'Analog'
    if digital:
        iface_map = {0: 'eDP/LVDS', 1: 'HDMIa', 2: 'HDMIb', 4: 'MDDI', 5: 'DisplayPort'}
        iface = iface_map.get(data[24] & 0x0F, 'Digital')

    monitor_name = ''
    for i in range(4):
        off = 54 + i * 18
        if off + 18 <= len(data) and data[off + 3] == 0xFC:
            monitor_name = data[off+5:off+18].decode('ascii', errors='ignore').strip().rstrip('\n')

    reported_gamma = round(1 + data[23] / 100, 2) if data[23] != 0xFF else 2.2

    return dict(
        mfr_code=mfr,
        manufacturer=MANUFACTURERS.get(mfr, mfr),
        product_code=f'{product_code:04X}',
        monitor_name=monitor_name,
        resolution=(max(h_active, 1), max(v_active, 1)),
        size_mm=(h_mm, v_mm),
        diagonal_in=diag_in,
        digital=digital,
        interface=iface,
        reported_gamma=reported_gamma,
    )

def _xrandr_connected_outputs() -> dict[str, tuple]:
    """
    Return {output_name: (width, height, x, y)} for all connected xrandr outputs.
    x, y is the monitor's top-left position in the virtual desktop (0,0 for primary).
    """
    result: dict[str, tuple] = {}
    try:
        out = subprocess.check_output(['xrandr'], text=True, timeout=5,
                                      env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')})
        for line in out.splitlines():
            # Active: "HDMI-1 connected primary 1920x1080+1920+0 ..."
            m = re.match(r'^(\S+) connected(?: primary)? (\d+)x(\d+)\+(\d+)\+(\d+)', line)
            if m:
                result[m.group(1)] = (int(m.group(2)), int(m.group(3)),
                                      int(m.group(4)), int(m.group(5)))
                continue
            # Connected but not active (no resolution in line)
            m2 = re.match(r'^(\S+) connected\b', line)
            if m2 and m2.group(1) not in result:
                result[m2.group(1)] = (1920, 1080, 0, 0)
    except Exception:
        pass
    return result

def _normalize(name: str) -> str:
    """Canonical form for fuzzy name matching: uppercase, drop dashes."""
    return re.sub(r'[-_]', '', name).upper()

def _reconcile_name(candidate: str, xrandr_outputs: dict[str, tuple]) -> str | None:
    """
    Find the best matching xrandr output name for a candidate derived from /sys.
    Handles common driver mismatches:
      HDMI-A-1  ↔  HDMI-1  ↔  HDMI1
      DP-1      ↔  DP1
    Returns the matched xrandr name, or None if no match found.
    """
    if candidate in xrandr_outputs:
        return candidate  # exact match

    c_norm = _normalize(candidate)
    for name in xrandr_outputs:
        if _normalize(name) == c_norm:
            return name

    # Structural variants: HDMI-A-1 → try HDMI-1, HDMI1
    variants: list[str] = []
    # Drop the '-A' or '-B' segment (Intel vs AMD naming)
    v = re.sub(r'-[A-Z]-', '-', candidate)
    variants.append(v)
    # Drop all dashes (e.g. HDMI-1 → HDMI1)
    variants.append(re.sub(r'-', '', candidate))
    # Add dash before trailing digit (HDMI1 → HDMI-1)
    variants.append(re.sub(r'(\D)(\d+)$', r'\1-\2', candidate))

    for v in variants:
        if v in xrandr_outputs:
            return v
        vn = _normalize(v)
        for name in xrandr_outputs:
            if _normalize(name) == vn:
                return name

    # Last resort: same port type prefix (e.g. both start with HDMI)
    prefix = re.match(r'^[A-Za-z]+', candidate)
    if prefix:
        pfx = prefix.group(0).upper()
        matches = [n for n in xrandr_outputs if n.upper().startswith(pfx)]
        if len(matches) == 1:
            return matches[0]

    return None

def detect_displays() -> list[dict]:
    """
    Detect connected displays via EDID + xrandr reconciliation.
    Returns displays with verified xrandr_name values that will work with
    xrandr --output commands.
    """
    xrandr_outputs = _xrandr_connected_outputs()
    displays: list[dict] = []
    drm = Path('/sys/class/drm')
    edid_errors: list[str] = []

    for connector_dir in sorted(drm.glob('*')):
        edid_path = connector_dir / 'edid'
        status_path = connector_dir / 'status'

        # Skip disconnected ports (avoids reading empty EDID files)
        try:
            status = status_path.read_text().strip()
            if status != 'connected':
                continue
        except Exception:
            pass  # status file missing — try anyway

        try:
            raw = edid_path.read_bytes()
        except PermissionError:
            edid_errors.append(f'Permission denied: {edid_path}')
            continue
        except FileNotFoundError:
            continue
        except Exception as e:
            edid_errors.append(f'{edid_path}: {e}')
            continue

        info = parse_edid(raw)
        sys_name = connector_dir.name

        # Derive candidate xrandr name from sys name (card0-HDMI-A-1 → HDMI-A-1)
        parts = sys_name.split('-', 1)
        candidate = parts[1] if len(parts) > 1 else sys_name

        # Reconcile against actual xrandr output names
        xrandr_name = _reconcile_name(candidate, xrandr_outputs)

        if not xrandr_name:
            # Output not in xrandr (disconnected/not active) — skip
            continue

        if info is None:
            # EDID unreadable but xrandr knows about it — create minimal entry
            w, h = xrandr_outputs[xrandr_name]
            info = dict(
                mfr_code='UNK', manufacturer='Unknown',
                product_code='0000', monitor_name=xrandr_name,
                resolution=(w, h), size_mm=(0, 0), diagonal_in=0.0,
                digital=True, interface='Unknown', reported_gamma=2.2,
            )
        else:
            # Use xrandr's active resolution if EDID resolution looks wrong
            xr_entry = xrandr_outputs[xrandr_name]
            xr_w, xr_h = xr_entry[0], xr_entry[1]
            if xr_w > 100 and xr_h > 100:
                info['resolution'] = (xr_w, xr_h)

        xr_entry  = xrandr_outputs[xrandr_name]
        xr_x, xr_y = (xr_entry[2], xr_entry[3]) if len(xr_entry) >= 4 else (0, 0)
        info.update(
            sys_name=sys_name,
            xrandr_name=xrandr_name,
            xrandr_x=xr_x,
            xrandr_y=xr_y,
            is_internal='eDP' in sys_name or 'LVDS' in sys_name,
            edid_errors=edid_errors,
        )
        displays.append(info)

    # Fallback: build from xrandr if /sys gave nothing
    if not displays:
        displays = _xrandr_fallback(xrandr_outputs)

    # Deduplicate by xrandr_name
    seen: set[str] = set()
    unique: list[dict] = []
    for d in displays:
        n = d['xrandr_name']
        if n not in seen:
            seen.add(n)
            unique.append(d)

    return unique

def _xrandr_fallback(xrandr_outputs: dict | None = None) -> list[dict]:
    """Build display list purely from xrandr when /sys EDID is unavailable."""
    if xrandr_outputs is None:
        xrandr_outputs = _xrandr_connected_outputs()
    displays = []
    for name, entry in xrandr_outputs.items():
        w, h = entry[0], entry[1]
        x, y = (entry[2], entry[3]) if len(entry) >= 4 else (0, 0)
        displays.append(dict(
            sys_name=name, xrandr_name=name,
            mfr_code='UNK', manufacturer='Unknown',
            product_code='0000', monitor_name=name,
            resolution=(w, h), size_mm=(0, 0), diagonal_in=0.0,
            digital=True,
            interface='eDP' if 'eDP' in name else 'HDMI' if 'HDMI' in name else 'Unknown',
            is_internal='eDP' in name or 'LVDS' in name,
            reported_gamma=2.2,
            edid_errors=[],
            xrandr_x=x, xrandr_y=y,
        ))
    return displays

# ── Profile Engine ──────────────────────────────────────────────────────────────

def compute_profile(display: dict) -> dict:
    panel = PANEL_DB.get(display.get('mfr_code', 'UNK'), DEFAULT_PANEL)
    bleed, gradient, clouding = panel['bleed'], panel['gradient'], panel['clouding']
    severity = bleed * 0.50 + gradient * 0.30 + clouding * 0.20

    if severity >= 0.60:
        technique           = 'combined'
        overlay_alpha       = min(0.22, bleed * 0.28)
        edge_width          = 0.20
        brightness          = max(0.80, 1.0 - bleed * 0.22)
        gamma               = round(1.0 + gradient * 0.12, 3)
    elif severity >= 0.40:
        technique           = 'edge_mask'
        overlay_alpha       = min(0.16, bleed * 0.20)
        edge_width          = 0.15
        brightness          = max(0.88, 1.0 - bleed * 0.12)
        gamma               = 1.0
    elif severity >= 0.20:
        technique           = 'gamma_darken'
        overlay_alpha       = 0.0
        edge_width          = 0.10
        brightness          = max(0.92, 1.0 - bleed * 0.08)
        gamma               = round(1.0 + gradient * 0.06, 3)
    else:
        technique           = 'minimal'
        overlay_alpha       = 0.0
        edge_width          = 0.0
        brightness          = 1.0
        gamma               = 1.0

    return dict(
        panel=panel,
        severity=round(severity, 3),
        technique=technique,
        params=dict(
            brightness=round(brightness, 3),
            gamma_r=round(gamma, 3),
            gamma_g=round(gamma * 0.99, 3),
            gamma_b=round(gamma * 1.01, 3),
            overlay_alpha=round(overlay_alpha, 3),
            edge_width=round(edge_width, 3),
        ),
    )

# ── Gamma Correction ────────────────────────────────────────────────────────────

def apply_gamma(name: str, brightness: float, gr: float, gg: float, gb: float) -> tuple:
    """Returns (success: bool, error_msg: str, gamma_unsupported: bool)."""
    cmd = [
        'xrandr', '--output', name,
        '--brightness', f'{brightness:.3f}',
        '--gamma', f'{gr:.3f}:{gg:.3f}:{gb:.3f}',
    ]
    try:
        env = {**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')}
        r = subprocess.run(cmd, timeout=5, capture_output=True, text=True, env=env)
        if r.returncode == 0:
            return True, '', False
        err = (r.stderr or r.stdout).strip()
        # Detect gamma-unsupported condition (modesetting driver / HDMI limitation)
        gamma_unsupported = any(kw in err.lower() for kw in
                                ('gamma', 'crtc', 'randr', 'failed to get size'))
        return False, err or f'xrandr exited {r.returncode}', gamma_unsupported
    except FileNotFoundError:
        return False, 'xrandr not found — install x11-xserver-utils', False
    except subprocess.TimeoutExpired:
        return False, 'xrandr timed out', False
    except Exception as e:
        return False, str(e), False

def reset_gamma(name: str):
    apply_gamma(name, 1.0, 1.0, 1.0, 1.0)  # errors silently ignored on reset

# ── Overlay Window ──────────────────────────────────────────────────────────────

class OverlayWindow(Gtk.Window):
    def __init__(self, resolution: tuple, position: tuple, technique: str, params: dict):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.technique = technique
        self.params    = params

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_app_paintable(True)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)

        w, h = resolution
        px, py = position
        self.set_default_size(w, h)
        self.move(px, py)  # position on the correct monitor

        self.connect('draw',    self._on_draw)
        self.connect('realize', self._on_realize)

    def _on_realize(self, _):
        self.input_shape_combine_region(cairo.Region())

    def update(self, technique: str, params: dict):
        self.technique = technique
        self.params    = params
        self.queue_draw()

    def _on_draw(self, _, cr):
        w, h     = self.get_size()
        alpha    = self.params.get('overlay_alpha', 0.12)
        edge_w   = self.params.get('edge_width', 0.15)
        tech     = self.technique

        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        if tech in ('edge_mask', 'combined'):
            self._paint_edges(cr, w, h, alpha, edge_w)
        if tech == 'uniformity_mask':
            self._paint_radial(cr, w, h, alpha)

    def _paint_edges(self, cr, w, h, alpha, ef):
        ew, eh = w * ef, h * ef

        for x0, y0, x1, y1 in [
            (0,  0,  ew,      0),
            (w,  0,  w - ew,  0),
            (0,  0,  0,       eh),
            (0,  h,  0,       h - eh),
        ]:
            g = cairo.LinearGradient(x0, y0, x1, y1)
            g.add_color_stop_rgba(0, 0, 0, alpha, alpha)
            g.add_color_stop_rgba(1, 0, 0, 0,     0)
            cr.set_source(g)
            cr.paint()

        cr_size = min(ew, eh) * 1.8
        for cx, cy in [(0, 0), (w, 0), (0, h), (w, h)]:
            g = cairo.RadialGradient(cx, cy, 0, cx, cy, cr_size)
            g.add_color_stop_rgba(0, 0, 0, alpha * 1.4, alpha * 1.4)
            g.add_color_stop_rgba(1, 0, 0, 0,           0)
            cr.set_source(g)
            cr.paint()

    def _paint_radial(self, cr, w, h, alpha):
        cx, cy = w / 2, h / 2
        r = math.sqrt(cx**2 + cy**2)
        g = cairo.RadialGradient(cx, cy, 0, cx, cy, r)
        g.add_color_stop_rgba(0.0, 0, 0, 0,     0)
        g.add_color_stop_rgba(0.65, 0, 0, 0,    0)
        g.add_color_stop_rgba(1.0, 0, 0, alpha, alpha)
        cr.set_source(g)
        cr.paint()

# ── Config ──────────────────────────────────────────────────────────────────────

CONFIG_DIR  = Path.home() / '.config' / 'luminafix'
CONFIG_FILE = CONFIG_DIR / 'profiles.json'

def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}

def save_config(data: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))

# ── GTK3 UI ─────────────────────────────────────────────────────────────────────

CSS = b"""
* {
    -gtk-icon-style: regular;
}
window {
    background-color: #12121f;
    color: #d0d0e8;
}
.header {
    background-color: #0d1117;
    border-bottom: 2px solid #e94560;
    padding: 14px 20px 10px 20px;
}
.app-title {
    font-size: 20px;
    font-weight: bold;
    color: #e94560;
}
.app-sub {
    font-size: 11px;
    color: #606080;
}
.card {
    background-color: #1a1a2e;
    border-radius: 6px;
    border: 1px solid #252545;
    padding: 14px;
    margin: 4px;
}
.chip-label {
    font-size: 9px;
    font-weight: bold;
    color: #e94560;
    letter-spacing: 1px;
}
.val-label {
    font-size: 12px;
    color: #b0b0c8;
}
.display-row {
    padding: 8px 12px;
    border-bottom: 1px solid #252545;
}
.display-name {
    font-size: 13px;
    font-weight: bold;
    color: #d0d0e8;
}
.display-meta {
    font-size: 11px;
    color: #707090;
}
.sev-good     { color: #4ade80; font-weight: bold; }
.sev-moderate { color: #facc15; font-weight: bold; }
.sev-high     { color: #f87171; font-weight: bold; }
.technique-card {
    background-color: #16213e;
    border-radius: 6px;
    border-left: 3px solid #e94560;
    padding: 10px 14px;
    margin: 4px;
}
.tech-title { font-size: 12px; font-weight: bold; color: #e0e0f0; }
.tech-desc  { font-size: 11px; color: #8080a0; }
.btn-primary {
    background-color: #e94560;
    color: #ffffff;
    border: none;
    border-radius: 5px;
    padding: 7px 18px;
    font-weight: bold;
    font-size: 12px;
}
.btn-secondary {
    background-color: #252545;
    color: #9090b0;
    border: 1px solid #353565;
    border-radius: 5px;
    padding: 7px 16px;
    font-size: 12px;
}
.slider-label {
    font-size: 11px;
    color: #8888aa;
}
.status-bar {
    background-color: #0d1117;
    border-top: 1px solid #252545;
    padding: 6px 14px;
}
.status-text { font-size: 11px; color: #606080; }
notebook tab {
    background-color: #1a1a2e;
    color: #707090;
    padding: 8px 18px;
    border: none;
}
notebook tab:checked {
    background-color: #252545;
    color: #e0e0f0;
    border-bottom: 2px solid #e94560;
}
"""

def sev_class(val: float) -> tuple[str, str]:
    if val < 0.33:
        return 'sev-good',     f'Low ({int(val*100)}%)'
    elif val < 0.60:
        return 'sev-moderate', f'Moderate ({int(val*100)}%)'
    else:
        return 'sev-high',     f'High ({int(val*100)}%)'


class LuminaFix(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='io.luminafix.app')
        self.displays:   list[dict] = []
        self.selected:   dict | None = None
        self.profile:    dict | None = None
        self.overlay:    OverlayWindow | None = None
        self.active:     bool = False
        self.config:     dict = load_config()

    def do_activate(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self.win = Gtk.ApplicationWindow(application=self)
        self.win.set_title('LuminaFix')
        self.win.set_default_size(800, 600)
        self.win.set_resizable(False)
        self.win.connect('destroy', self._on_destroy)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.win.add(root)

        # ── Header ──
        hdr = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        hdr.get_style_context().add_class('header')
        t = Gtk.Label(label='LuminaFix')
        t.get_style_context().add_class('app-title')
        t.set_halign(Gtk.Align.START)
        s = Gtk.Label(label='Display Uniformity Corrector  ·  Linux Edition')
        s.get_style_context().add_class('app-sub')
        s.set_halign(Gtk.Align.START)
        hdr.pack_start(t, False, False, 0)
        hdr.pack_start(s, False, False, 2)
        root.pack_start(hdr, False, False, 0)

        # ── Notebook ──
        nb = Gtk.Notebook()
        nb.set_border_width(0)
        nb.append_page(self._build_detect(),  Gtk.Label(label='  Detect  '))
        nb.append_page(self._build_profile(), Gtk.Label(label='  Profile  '))
        nb.append_page(self._build_correct(), Gtk.Label(label='  Correct  '))
        root.pack_start(nb, True, True, 0)

        # ── Status bar ──
        sb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        sb.get_style_context().add_class('status-bar')
        self._status = Gtk.Label(label='Ready — click Scan to detect displays.')
        self._status.get_style_context().add_class('status-text')
        self._status.set_halign(Gtk.Align.START)
        sb.pack_start(self._status, True, True, 0)
        root.pack_end(sb, False, False, 0)

        self.win.show_all()
        GLib.idle_add(self._do_scan)

    # ── Detect Tab ──────────────────────────────────────────────────────────────

    def _build_detect(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(14)

        # List
        self._dlist = Gtk.ListBox()
        self._dlist.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._dlist.connect('row-selected', self._on_row_selected)
        self._dlist.get_style_context().add_class('card')

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(160)
        sw.add(self._dlist)
        box.pack_start(sw, False, False, 0)

        # Info grid
        grid = Gtk.Grid()
        grid.get_style_context().add_class('card')
        grid.set_column_spacing(20)
        grid.set_row_spacing(8)
        grid.set_border_width(4)

        self._info = {}
        fields = [
            ('Manufacturer', 'mfr'),  ('Interface',  'iface'),
            ('Model',        'model'), ('Gamma',      'gamma'),
            ('Resolution',   'res'),   ('Size',       'size'),
            ('Product Code', 'prod'),  ('Internal',   'internal'),
        ]
        for i, (lbl_text, key) in enumerate(fields):
            row, col = divmod(i, 2)
            chip = Gtk.Label(label=lbl_text.upper())
            chip.get_style_context().add_class('chip-label')
            chip.set_halign(Gtk.Align.END)
            val = Gtk.Label(label='—')
            val.get_style_context().add_class('val-label')
            val.set_halign(Gtk.Align.START)
            grid.attach(chip, col * 2,     row, 1, 1)
            grid.attach(val,  col * 2 + 1, row, 1, 1)
            self._info[key] = val

        box.pack_start(grid, False, False, 0)

        # Scan button
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn = Gtk.Button(label='↺  Scan Displays')
        btn.get_style_context().add_class('btn-primary')
        btn.connect('clicked', lambda _: self._do_scan())
        row.pack_end(btn, False, False, 0)
        box.pack_start(row, False, False, 0)

        return box

    # ── Profile Tab ─────────────────────────────────────────────────────────────

    def _build_profile(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(14)

        # Severity bar
        sev_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        sev_box.get_style_context().add_class('card')

        self._sev = {}
        for metric in ('Backlight Bleed', 'Gradient', 'Clouding', 'Overall'):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            col.set_border_width(8)
            chip = Gtk.Label(label=metric.upper())
            chip.get_style_context().add_class('chip-label')
            chip.set_halign(Gtk.Align.CENTER)
            val = Gtk.Label(label='—')
            val.set_halign(Gtk.Align.CENTER)
            col.pack_start(chip, False, False, 0)
            col.pack_start(val,  False, False, 0)
            sev_box.pack_start(col, True, True, 0)
            self._sev[metric] = val

        box.pack_start(sev_box, False, False, 0)

        # Panel notes
        notes_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        notes_card.get_style_context().add_class('card')
        nc = Gtk.Label(label='PANEL NOTES')
        nc.get_style_context().add_class('chip-label')
        nc.set_halign(Gtk.Align.START)
        self._notes = Gtk.Label(label='Scan displays to load profile.')
        self._notes.set_line_wrap(True)
        self._notes.set_xalign(0)
        self._notes.get_style_context().add_class('val-label')
        notes_card.pack_start(nc, False, False, 0)
        notes_card.pack_start(self._notes, False, False, 6)
        box.pack_start(notes_card, False, False, 0)

        # Recommended technique
        self._tech_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._tech_card.get_style_context().add_class('technique-card')
        self._tech_title = Gtk.Label(label='—')
        self._tech_title.get_style_context().add_class('tech-title')
        self._tech_title.set_halign(Gtk.Align.START)
        self._tech_desc  = Gtk.Label(label='')
        self._tech_desc.get_style_context().add_class('tech-desc')
        self._tech_desc.set_line_wrap(True)
        self._tech_desc.set_xalign(0)
        tc = Gtk.Label(label='RECOMMENDED TECHNIQUE')
        tc.get_style_context().add_class('chip-label')
        tc.set_halign(Gtk.Align.START)
        self._tech_card.pack_start(tc,              False, False, 0)
        self._tech_card.pack_start(self._tech_title, False, False, 4)
        self._tech_card.pack_start(self._tech_desc,  False, False, 0)
        box.pack_start(self._tech_card, False, False, 0)

        return box

    # ── Correct Tab ─────────────────────────────────────────────────────────────

    def _build_correct(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(14)

        sliders_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sliders_card.get_style_context().add_class('card')

        self._sliders: dict[str, Gtk.Scale] = {}
        defs = [
            ('Brightness',      'brightness',    0.50, 1.00, 1.000),
            ('Gamma  R',        'gamma_r',        0.80, 1.50, 1.000),
            ('Gamma  G',        'gamma_g',        0.80, 1.50, 1.000),
            ('Gamma  B',        'gamma_b',        0.80, 1.50, 1.000),
            ('Edge Mask Alpha', 'overlay_alpha',  0.00, 0.40, 0.000),
            ('Edge Width',      'edge_width',     0.05, 0.35, 0.150),
        ]
        for label_text, key, lo, hi, default in defs:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            lbl = Gtk.Label(label=f'{label_text}')
            lbl.set_width_chars(17)
            lbl.set_halign(Gtk.Align.END)
            lbl.get_style_context().add_class('slider-label')
            adj = Gtk.Adjustment(value=default, lower=lo, upper=hi,
                                 step_increment=0.001, page_increment=0.01)
            sl = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
            sl.set_hexpand(True)
            sl.set_digits(3)
            sl.connect('value-changed', self._on_param_changed)
            row.pack_start(lbl, False, False, 0)
            row.pack_start(sl,  True,  True,  0)
            sliders_card.pack_start(row, False, False, 0)
            self._sliders[key] = sl

        box.pack_start(sliders_card, False, False, 0)

        # Buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._apply_btn = Gtk.Button(label='▶  Apply')
        self._apply_btn.get_style_context().add_class('btn-primary')
        self._apply_btn.connect('clicked', self._on_apply)

        self._reset_btn = Gtk.Button(label='↺  Reset')
        self._reset_btn.get_style_context().add_class('btn-secondary')
        self._reset_btn.connect('clicked', self._on_reset)

        self._save_btn = Gtk.Button(label='💾  Save Profile')
        self._save_btn.get_style_context().add_class('btn-secondary')
        self._save_btn.connect('clicked', self._on_save)

        self._load_btn = Gtk.Button(label='📂  Load Profile')
        self._load_btn.get_style_context().add_class('btn-secondary')
        self._load_btn.connect('clicked', self._on_load)

        btn_row.pack_end(self._apply_btn, False, False, 0)
        btn_row.pack_end(self._reset_btn, False, False, 0)
        btn_row.pack_end(self._save_btn,  False, False, 0)
        btn_row.pack_end(self._load_btn,  False, False, 0)
        box.pack_end(btn_row, False, False, 0)

        return box

    # ── Scan / Detection ────────────────────────────────────────────────────────

    def _do_scan(self) -> bool:
        self._set_status('Scanning EDID from /sys/class/drm …')
        self.displays = detect_displays()

        for child in self._dlist.get_children():
            self._dlist.remove(child)

        if not self.displays:
            self._set_status('No displays detected. Check /sys/class/drm permissions.')
            return False

        for d in self.displays:
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            content.set_border_width(8)
            content.get_style_context().add_class('display-row')

            icon  = '⬜' if d.get('is_internal') else '🔌'
            name  = Gtk.Label(label=f"{icon}  {d['xrandr_name']}")
            name.get_style_context().add_class('display-name')
            name.set_halign(Gtk.Align.START)

            w, h  = d['resolution']
            meta  = Gtk.Label(label=f"{d.get('manufacturer','?')}  ·  {w}×{h}")
            meta.get_style_context().add_class('display-meta')
            meta.set_halign(Gtk.Align.END)

            content.pack_start(name, True, True, 0)
            content.pack_end(meta, False, False, 0)
            content._display = d
            self._dlist.add(content)

        self._dlist.show_all()

        # Auto-select first internal
        for i, d in enumerate(self.displays):
            if d.get('is_internal'):
                row = self._dlist.get_row_at_index(i)
                if row:
                    self._dlist.select_row(row)
                break
        else:
            row = self._dlist.get_row_at_index(0)
            if row:
                self._dlist.select_row(row)

        edid_errs = [e for d in self.displays for e in d.get('edid_errors', [])]
        if edid_errs:
            self._set_status(f'Found {len(self.displays)} display(s). '
                             f'EDID warning: {edid_errs[0]} — '
                             f'fix: sudo chmod a+r /sys/class/drm/*/edid')
        else:
            self._set_status(f'Found {len(self.displays)} display(s).')
        return False

    def _on_row_selected(self, listbox, row):
        if row is None:
            return
        child = row.get_child()
        if not hasattr(child, '_display'):
            return
        self.selected = child._display
        d = self.selected

        self._info['mfr'].set_text(d.get('manufacturer', '—'))
        self._info['model'].set_text(d.get('monitor_name') or '—')
        self._info['prod'].set_text(d.get('product_code', '—'))
        w, h = d.get('resolution', (0, 0))
        self._info['res'].set_text(f'{w} × {h}')
        hm, vm = d.get('size_mm', (0, 0))
        diag = d.get('diagonal_in', 0)
        size_str = f'{diag}"' if diag else '—'
        if hm and vm:
            size_str += f'  ({hm}×{vm} mm)'
        self._info['size'].set_text(size_str)
        self._info['iface'].set_text(d.get('interface', '—'))
        self._info['gamma'].set_text(str(d.get('reported_gamma', '—')))
        self._info['internal'].set_text('Yes' if d.get('is_internal') else 'No')

        self.profile = compute_profile(d)
        self._refresh_profile()
        self._load_params()

    # ── Profile Tab Refresh ──────────────────────────────────────────────────────

    def _refresh_profile(self):
        if not self.profile:
            return
        panel = self.profile['panel']

        for metric, key in [
            ('Backlight Bleed', 'bleed'),
            ('Gradient',        'gradient'),
            ('Clouding',        'clouding'),
        ]:
            cls, text = sev_class(panel[key])
            lbl = self._sev[metric]
            lbl.set_text(text)
            for c in ('sev-good', 'sev-moderate', 'sev-high'):
                lbl.get_style_context().remove_class(c)
            lbl.get_style_context().add_class(cls)

        cls, text = sev_class(self.profile['severity'])
        ol = self._sev['Overall']
        ol.set_text(text)
        for c in ('sev-good', 'sev-moderate', 'sev-high'):
            ol.get_style_context().remove_class(c)
        ol.get_style_context().add_class(cls)

        self._notes.set_text(panel.get('notes', '—'))

        tech = self.profile['technique']
        title, desc = TECHNIQUE_INFO.get(tech, (tech, ''))
        self._tech_title.set_text(title)
        self._tech_desc.set_text(desc)

    # ── Correction Tab Logic ─────────────────────────────────────────────────────

    def _load_params(self):
        if not self.profile:
            return
        params = self.profile['params'].copy()
        key = self.selected.get('xrandr_name', '') if self.selected else ''
        if key in self.config:
            params.update(self.config[key])
        self._block = True
        for k, sl in self._sliders.items():
            if k in params:
                sl.set_value(params[k])
        self._block = False

    def _get_params(self) -> dict:
        return {k: round(sl.get_value(), 3) for k, sl in self._sliders.items()}

    def _on_param_changed(self, _):
        if getattr(self, '_block', False) or not self.active:
            return
        if not self.selected or not self.profile:
            return
        p = self._get_params()
        ok, err, gamma_unsupported = apply_gamma(
            self.selected['xrandr_name'],
            p['brightness'], p['gamma_r'], p['gamma_g'], p['gamma_b'])
        if not ok and err and not gamma_unsupported:
            self._set_status(f'⚠ {err}')
        tech = self.profile['technique']
        if self.overlay and p.get('overlay_alpha', 0) > 0.005:
            self.overlay.update(tech, p)
        elif self.overlay and p.get('overlay_alpha', 0) <= 0.005:
            self.overlay.hide()

    def _on_apply(self, _):
        if not self.selected or not self.profile:
            self._set_status('Select a display first.')
            return
        p    = self._get_params()
        tech = self.profile['technique']
        name = self.selected['xrandr_name']

        ok, err, gamma_unsupported = apply_gamma(
            name, p['brightness'], p['gamma_r'], p['gamma_g'], p['gamma_b'])

        # If gamma unsupported (common on HDMI/TV outputs via modesetting),
        # auto-escalate to overlay-only and bump alpha so correction is visible.
        if not ok and gamma_unsupported:
            if p.get('overlay_alpha', 0) < 0.05:
                p['overlay_alpha'] = max(0.10, self.profile['params'].get('overlay_alpha', 0.10))
                self._sliders['overlay_alpha'].set_value(p['overlay_alpha'])
            tech = 'edge_mask' if tech == 'gamma_darken' else tech
            if tech == 'minimal':
                tech = 'edge_mask'

        res  = self.selected['resolution']
        pos  = (self.selected.get('xrandr_x', 0), self.selected.get('xrandr_y', 0))
        use_overlay = tech in ('edge_mask', 'combined', 'uniformity_mask') \
                      and p.get('overlay_alpha', 0) > 0.005

        if use_overlay:
            if self.overlay:
                self.overlay.update(tech, p)
                self.overlay.move(*pos)
                self.overlay.show_all()
            else:
                self.overlay = OverlayWindow(res, pos, tech, p)
                self.overlay.show_all()
        else:
            if self.overlay:
                self.overlay.hide()

        self.active = True

        if ok:
            self._set_status(f'✓ Correction active on {name}')
        elif gamma_unsupported:
            self._set_status(
                f'ℹ Gamma not supported on {name} (modesetting/HDMI limitation) — '
                f'overlay-only correction applied.'
            )
        else:
            tip = ''
            if 'BadName' in err or 'output' in err.lower():
                tip = '  (output name mismatch — rescan)'
            elif 'permission' in err.lower() or 'auth' in err.lower():
                tip = '  (try: xhost +local: or check DISPLAY env)'
            self._set_status(f'⚠ {err}{tip}')

    def _on_reset(self, _):
        if self.selected:
            reset_gamma(self.selected['xrandr_name'])  # errors ignored on reset
        if self.overlay:
            self.overlay.destroy()
            self.overlay = None
        self.active = False
        self._set_status('Reset — display back to native settings.')

    def _on_save(self, _):
        if not self.selected:
            return
        key = self.selected.get('xrandr_name', 'default')
        self.config[key] = self._get_params()
        save_config(self.config)
        self._set_status(f'Profile saved → {CONFIG_FILE}')

    def _on_load(self, _):
        if not self.selected:
            return
        key = self.selected.get('xrandr_name', 'default')
        if key not in self.config:
            self._set_status(f'No saved profile for {key}.')
            return
        self._block = True
        for k, sl in self._sliders.items():
            if k in self.config[key]:
                sl.set_value(self.config[key][k])
        self._block = False
        self._set_status(f'Loaded profile for {key}.')

    def _on_destroy(self, _):
        if self.overlay:
            self.overlay.destroy()
        self.quit()

    def _set_status(self, msg: str):
        self._status.set_text(msg)


# ── Entry ────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = LuminaFix()
    sys.exit(app.run(sys.argv))
