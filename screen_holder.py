"""
Board game player screen holder generator.

Generates 3D-printable holders for tri-fold player screens (a centre panel with two angled wings):
  * a base with slots for the screen and two rounded token trays (closed front, open front, or floorless)
  * optional lids for the closed-front base: plain, crown grip, or bar grip
  * glue-free name plaques that slide onto a dovetail rail on the table-facing side, as two-part
    3MF files for two-colour printing (plate + text)

Usage
  pip install -r requirements.txt
  python screen_holder.py games/kings-dilemma.toml                       # -> out/kings-dilemma/
  python screen_holder.py games/kings-dilemma.toml --names "Alice" "Bob" # your group's names
  python screen_holder.py --list-settings                                # settings usable in [advanced]

Besides STLs and two-part plaque 3MFs, every run writes ready-to-print Bambu Studio projects to
out/<game>/bambu-studio/ (bases, lids, name plates), built from the printed projects in templates/
(see bambu.py).

Each game has its own config file in games/ (see games/kings-dilemma.toml). To add a game, copy that
file, measure your screen and change the numbers - there is no need to edit this script. The values
below are the built-in defaults that a config overrides.

The default settings fit The King's Dilemma. Design based on "King's Dilemma - Dashboard" by
Frankenduke (printables.com/model/872481, CC BY 4.0).

Coordinates (base): x = left/right, y = 0 at the table-facing side (under the screen), growing towards
the player; z = up. All units mm.
"""
import argparse, math, os, re, sys, zipfile
import numpy as np
import trimesh
from manifold3d import Manifold, CrossSection, FillRule, JoinType
from shapely.geometry import Polygon, LineString, box
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

# ================================================================ DEFAULTS (overridden by a config file)
# The player screen: a centre panel with a wing on each side, folded towards the player.
SCREEN_CENTRE = 127.0   # width of the centre panel (mm)
SCREEN_WING = 64.0      # width of each wing (mm)
SCREEN_ANGLE = 60.0     # angle between the centre panel and each wing, measured from the centre panel's line (deg)
SCREEN_T = 1.8          # slot width: screen thickness + ~0.3 mm (King's Dilemma House card is ~1.5 mm)

H = 25.0                # base height (also the plaque height)
D = 63.0                # base depth, table side -> player side. Must be more than SCREEN_WING * sin(SCREEN_ANGLE) + ~5

NAMES = ["Player 1", "Player 2", "Player 3", "Player 4"]   # usually given with --names on the command line
FONT = "fonts/CinzelDecorative-Bold.ttf"   # any .ttf/.otf; relative to the config file or this script

# ================================================================ FIT AND DETAILS
SLOT_DEPTH = 6.0        # how deep the screen sits
SIDE_WALL = 2.4         # wall outside the screen slots

LEDGE_GAP = 2.0         # wall between screen slot and lid recess
LID_T = 2.4             # lid thickness
LID_CLEAR = 0.25        # lid clearance per side - raise if the lid binds
LIP = 2.0               # ledge width the lid rests on
FRONT_WALL = 3.0        # closed front: wall on the player side, outside the lid recess
FLOOR = 2.0             # tray floor thickness
MID_WALL = 2.0          # wall between the two trays
TRAY_FILLET = 8.0       # radius of the rounded tray floor edges (tokens slide up them)
FRONT_LIP_ANGLE = 45.0  # open front: the rounded front slope is kept up to this steepness, then ends in a low lip
NOTCH_R = 9.0           # closed front: finger notch radius

# dovetail rail (on the base) / groove (in the plaque)
RAIL_W = 40.0           # rail width at the face
RAIL_H = 1.8            # rail protrusion
RAIL_U = 1.0            # dovetail undercut per side
RAIL_Z0 = 1.0           # rail starts this far above the table
RAIL_TOP_GAP = 2.0      # rail ends this far below the base top
FIT = 0.2               # plaque sliding clearance per side - raise to 0.25-0.3 if plaques are tight
PLATE_BACK = 1.6        # plaque material behind the groove

TEXT_INLAY_C = 0.6      # face-down plaque: text depth (0.6 = 3 layers at 0.2 mm)
TEXT_INLAY = 0.6        # small white plaque: text depth
TEXT_RAISE = 0.8        # raised-letter plaque: letter height

# ---------------------------------------------------------------- helpers


def cs(poly):
    """shapely (Multi)Polygon -> CrossSection"""
    polys = [poly] if poly.geom_type == "Polygon" else list(poly.geoms)
    loops = []
    for p in polys:
        p = p.buffer(0)
        ext = np.array(p.exterior.coords)[:-1]
        if Polygon(ext).exterior.is_ccw is False:
            ext = ext[::-1]
        loops.append(ext)
        for h in p.interiors:
            hh = np.array(h.coords)[:-1]
            if Polygon(hh).exterior.is_ccw:
                hh = hh[::-1]
            loops.append(hh)
    return CrossSection(loops, FillRule.Positive)


def prism(poly, z0, z1):
    return Manifold.extrude(cs(poly), z1 - z0).translate((0, 0, z0))


def rounded(p, r):
    return p.buffer(-r, join_style="mitre").buffer(r, resolution=16)


def to_trimesh(m):
    m = m.simplify(0.01)
    mesh = m.to_mesh()
    return trimesh.Trimesh(np.array(mesh.vert_properties)[:, :3], np.array(mesh.tri_verts), process=True)


def hull_layers(layers):
    return Manifold.batch_hull([prism(p, z, z + 0.01) for p, z in layers])


# ================================================================ game config files
CONFIG_DIR = None
# friendly config keys -> script settings
CONFIG_KEYS = {
    "screen": {"centre": "SCREEN_CENTRE", "wing": "SCREEN_WING", "angle": "SCREEN_ANGLE", "slot": "SCREEN_T"},
    "base": {"height": "H", "depth": "D"},
    "plaques": {"names": "NAMES", "font": "FONT"},
    "fit": {"plaque": "FIT", "lid": "LID_CLEAR"},
}
_DERIVED = {"YS", "RAIL_Z1", "PLATE_T", "S_ANG", "C_ANG", "COT_ANG", "Z_RECESS", "XB", "LIP_RISE", "PLAQUE_A_W",
            "L", "U", "TEXT_CLEAN", "HERE", "OUT", "CONFIG_DIR", "CONFIG_KEYS", "FONT_PATH"}


def _resolve(path):
    """font paths are relative to the config file first, then to this script"""
    if os.path.isabs(path):
        return path
    for base in (CONFIG_DIR, HERE):
        if base and os.path.exists(os.path.join(base, path)):
            return os.path.join(base, path)
    return os.path.join(HERE, path)


def tunables():
    """every plain numeric setting that can be overridden in a config's [advanced] table"""
    return sorted(k for k, v in globals().items()
                  if k.isupper() and k not in _DERIVED and isinstance(v, (int, float)) and not isinstance(v, bool))


def load_config(path):
    """apply a game config (TOML) on top of the defaults, then recompute the geometry"""
    global CONFIG_DIR
    try:
        import tomllib
    except ModuleNotFoundError:          # Python < 3.11
        import tomli as tomllib
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    CONFIG_DIR = os.path.dirname(os.path.abspath(path))
    errors = []
    for section, table in cfg.items():
        if section in ("name", "description", "source"):
            continue
        if not isinstance(table, dict):
            errors.append(f"unknown top-level key '{section}'")
            continue
        if section == "advanced":
            allowed = set(tunables())
            for k, v in table.items():
                if k not in allowed:
                    errors.append(f"[advanced] {k}: not a tunable setting (run: python screen_holder.py --list-settings)")
                else:
                    globals()[k] = float(v)
            continue
        if section not in CONFIG_KEYS:
            errors.append(f"unknown section [{section}] (expected one of {', '.join(CONFIG_KEYS)}, advanced)")
            continue
        for k, v in table.items():
            if k not in CONFIG_KEYS[section]:
                errors.append(f"[{section}] {k}: unknown key (expected one of {', '.join(CONFIG_KEYS[section])})")
                continue
            target = CONFIG_KEYS[section][k]
            globals()[target] = list(v) if target == "NAMES" else (v if target == "FONT" else float(v))
    if errors:
        sys.exit(f"{path}:\n  " + "\n  ".join(errors))
    _derive()
    return cfg


# ================================================================ derived geometry
TEXT_CLEAN = 0.0   # extra outline clean-up; raised automatically if a text mesh comes out non-manifold


def _derive():
    """(Re)compute everything that depends on the settings. Called at import and after loading a config."""
    global YS, RAIL_Z1, PLATE_T, S_ANG, C_ANG, COT_ANG, FONT_PATH, L, card_path, slot2d, U, outline, recess, \
        tray_zone, trays, XB, Z_RECESS, recess_open_ext, tray_zone_open, _lip_e, LIP_RISE, trays_open, lid2d_open, \
        PLAQUE_A_W
    YS = SCREEN_T / 2 + SIDE_WALL            # screen centre-line distance from the table-facing side
    RAIL_Z1 = H - RAIL_TOP_GAP
    PLATE_T = RAIL_H + FIT + PLATE_BACK      # plaque thickness (3.6)
    S_ANG, C_ANG = math.sin(math.radians(SCREEN_ANGLE)), math.cos(math.radians(SCREEN_ANGLE))
    COT_ANG = C_ANG / S_ANG                  # x shift per mm of y along the side planes
    FONT_PATH = _resolve(FONT)

    L = SCREEN_CENTRE / 2
    card_path = LineString([(-L - (SCREEN_WING + 1) * C_ANG, YS + (SCREEN_WING + 1) * S_ANG), (-L, YS), (L, YS),
                            (L + (SCREEN_WING + 1) * C_ANG, YS + (SCREEN_WING + 1) * S_ANG)])
    slot2d = card_path.buffer(SCREEN_T / 2, cap_style="flat", join_style="mitre")

    # region "inside the U" of the card, extended far towards the player
    far = 1000
    U = Polygon([(-L - far * C_ANG, YS + far * S_ANG), (-L, YS), (L, YS), (L + far * C_ANG, YS + far * S_ANG)])

    outline = U.buffer(SIDE_WALL + SCREEN_T / 2, join_style="mitre").intersection(box(-1000, 0, 1000, D))
    # round only the player-side corners
    corner_round = rounded(outline, 3.0)
    outline = Polygon([pt for pt in corner_round.exterior.coords if pt[1] > D / 2] +
                      [pt for pt in outline.exterior.coords if pt[1] <= D / 2]).convex_hull

    recess = U.buffer(-(SCREEN_T / 2 + LEDGE_GAP), join_style="mitre").intersection(box(-1000, -10, 1000, D - FRONT_WALL))
    recess = rounded(recess, 2.0)
    tray_zone = recess.buffer(-LIP, join_style="mitre")
    trays = [rounded(tray_zone.intersection(box(-1000, -10, -MID_WALL / 2, 1000)), 3.0),
             rounded(tray_zone.intersection(box(MID_WALL / 2, -10, 1000, 1000)), 3.0)]

    XB = max(x for x, y in outline.exterior.coords if abs(y) < 1e-6)   # half width of table-facing side
    Z_RECESS = H - LID_T - 0.1                                          # ledge height (lid top sits 0.1 below)



    # open-front base: trays run out through the front face and end in a low lip
    recess_open_ext = rounded(U.buffer(-(SCREEN_T / 2 + LEDGE_GAP), join_style="mitre")
                              .intersection(box(-1000, -10, 1000, D + 20)), 2.0)
    tray_zone_open = recess_open_ext.buffer(-LIP, join_style="mitre")
    _lip_e = TRAY_FILLET * (1 - math.sin(math.radians(90 - FRONT_LIP_ANGLE)))   # how far the tray edge sits beyond the face
    LIP_RISE = TRAY_FILLET * (1 - math.cos(math.radians(FRONT_LIP_ANGLE)))      # lip height above the tray floor
    trays_open = [rounded(tray_zone_open.intersection(box(-1000, -10, -MID_WALL / 2, 1000)), 3.0).intersection(box(-1000, -10, 1000, D + _lip_e)),
                  rounded(tray_zone_open.intersection(box(MID_WALL / 2, -10, 1000, 1000)), 3.0).intersection(box(-1000, -10, 1000, D + _lip_e))]
    lid2d_open = rounded(recess_open_ext.buffer(-LID_CLEAR).intersection(box(-1000, -10, 1000, D - LID_CLEAR)), 1.0)


    PLAQUE_A_W = 2 * XB - 14.8               # small plaque: a bit narrower than the front face


OPEN_SINK = 0.4     # floorless version: fillet centre sits this far below the table, so the
                    # slope meets the table at ~18 deg instead of a paper-thin tangent edge


def build_base(floor=True):
    body = prism(outline, 0, H)
    cut = prism(slot2d, H - SLOT_DEPTH, H + 1)
    cut += prism(recess, Z_RECESS, H + 1)
    for t in trays:
        r = TRAY_FILLET
        z0 = FLOOR if floor else -OPEN_SINK
        layers = [] if floor else [(t.buffer(-r), -1.0)]   # floorless: cut straight through
        for i in range(10):
            th = math.radians(90 * i / 9)
            inset = r * (1 - math.sin(th))
            z = z0 + r * (1 - math.cos(th))
            layers.append((t.buffer(-inset), z))
        layers.append((t, Z_RECESS + 0.5))
        cut += hull_layers(layers)
    # finger notch on the player side, centred, reaches just under the lid edge
    notch = Manifold.sphere(NOTCH_R, 64).scale((1.3, 1, 1)).translate((0, D - FRONT_WALL + 2.2, H + 4.0))
    cut += notch
    base = (body - cut).trim_by_plane((0, 0, 1), 0.0)
    # dovetail rail on the table-facing side
    rail = Polygon([(-RAIL_W / 2, 0.01), (RAIL_W / 2, 0.01), (RAIL_W / 2 + RAIL_U, -RAIL_H), (-RAIL_W / 2 - RAIL_U, -RAIL_H)])
    base += prism(rail, RAIL_Z0, RAIL_Z1 - 1.2)
    # chamfered top = lead-in for the plaque groove; the flat top is the stop
    base += hull_layers([(rail, RAIL_Z1 - 1.2 - 0.01), (rail.buffer(-0.6, join_style="mitre"), RAIL_Z1 - 0.01)])
    return base


def build_lid():
    lid2d = recess.buffer(-LID_CLEAR)
    lid = prism(lid2d, 0, LID_T)
    return lid


# ---------------------------------------------------------------- open-front base
# Player-side wall removed: the trays run out through the front face and end in a low lip, so a forward
# tilt pours the tokens out. Everything else (screen slots, ledges, middle wall, plaque rail) is unchanged.
HANDLE_LEN = 36.0   # grip bar length (left-right)
HANDLE_H = 8.0      # height above the lid top
HANDLE_W0 = 4.5     # bar width at its waist
HANDLE_W1 = 6.5     # bar width near the top (slight flare = something to pinch against)
HANDLE_FOOT = 1.5   # chamfered foot where the bar meets the lid


def build_base_open():
    body = prism(outline, 0, H)
    cut = prism(slot2d, H - SLOT_DEPTH, H + 1)
    cut += prism(recess_open_ext, Z_RECESS, H + 1)
    for t in trays_open:
        r = TRAY_FILLET
        layers = []
        for i in range(10):
            th = math.radians(90 * i / 9)
            inset = r * (1 - math.sin(th))
            z = FLOOR + r * (1 - math.cos(th))
            layers.append((t.buffer(-inset), z))
        layers.append((t, Z_RECESS + 0.5))
        cut += hull_layers(layers)
    base = (body - cut).trim_by_plane((0, 0, 1), 0.0)
    rail = Polygon([(-RAIL_W / 2, 0.01), (RAIL_W / 2, 0.01), (RAIL_W / 2 + RAIL_U, -RAIL_H), (-RAIL_W / 2 - RAIL_U, -RAIL_H)])
    base += prism(rail, RAIL_Z0, RAIL_Z1 - 1.2)
    base += hull_layers([(rail, RAIL_Z1 - 1.2 - 0.01), (rail.buffer(-0.6, join_style="mitre"), RAIL_Z1 - 0.01)])
    return base


def handle_at(cx, cy):
    """grip bar running left-right, standing on z=0 (the lid top), centred on (cx, cy).
    Cross-section: chamfered foot, slight outward flare (something to pinch against), rounded top."""
    f, w0, w1, h = HANDLE_FOOT, HANDLE_W0, HANDLE_W1, HANDLE_H
    pts = [(-(w0 / 2 + f), 0.0), (-w0 / 2, f), (-w1 / 2, h - 1.5)]
    for i in range(1, 8):                      # rounded top, radius 1.5 at the shoulders
        a = math.radians(90 * i / 8)
        pts.append((-w1 / 2 + 1.5 * (1 - math.cos(a)), h - 1.5 + 1.5 * math.sin(a)))
    pts = pts + [(-x, z) for x, z in reversed(pts)]
    prof = Polygon(pts)                         # (y, z) profile
    bar = Manifold.extrude(cs(prof), HANDLE_LEN).translate((0, 0, -HANDLE_LEN / 2))   # (y, z, x)
    bar = bar.transform(np.array([[0, 0, 1, 0], [1, 0, 0, 0], [0, 1, 0, 0]], dtype=float))  # -> (x, y, z)
    ends = LineString([(-(HANDLE_LEN - w1 - 2 * f) / 2, 0), ((HANDLE_LEN - w1 - 2 * f) / 2, 0)]).buffer(w1 / 2 + f, resolution=16)
    bar = bar ^ prism(ends, -1, h + 1)          # round the ends in plan
    return bar.translate((cx, cy, 0))


def build_lid_handle(lid2d):
    """lid with a grip bar over its balance point, so it lifts off level. Printed handle-up."""
    c = lid2d.centroid
    return prism(lid2d, 0, LID_T) + handle_at(0.0, c.y).translate((0, 0, LID_T - 0.01))


# ---------------------------------------------------------------- crown handle
CROWN_BAND_H = 5.5    # band height
CROWN_BAND_T = 6.0    # band thickness (front-back) - this is what you pinch
CROWN_POINT_T = 4.6   # thickness of the points above the band
CROWN_FOOT = 1.5      # chamfered foot where the band meets the lid


def _xz_extrude(poly, t):
    """extrude a silhouette drawn in (x, z) to thickness t along y, centred on y=0"""
    m = Manifold.extrude(cs(poly), t)                                    # (x, z, y)
    return m.transform(np.array([[1, 0, 0, 0], [0, 0, 1, -t / 2], [0, 1, 0, 0]], dtype=float))


def crown_at(cx, cy):
    """a flat king's crown standing on z=0, facing the player, centred on (cx, cy):
    flared band with jewels, five points in a zigzag tipped with balls, a small cross on the centre point"""
    from shapely.geometry import Point
    from shapely.ops import unary_union
    bh = CROWN_BAND_H
    wb, wt = 19.5, 20.6                     # half-width of the band at the bottom / top (slight flare)
    tips = [(-16.8, 11.2), (-8.4, 12.4), (0.0, 13.6), (8.4, 12.4), (16.8, 11.2)]
    valley = bh + 1.6
    outline = [(-wb, 0), (-wt, bh)]
    for i, (x, zt) in enumerate(tips):
        outline += [(x - 0.9, zt), (x + 0.9, zt)]
        if i < 4:
            outline.append(((x + tips[i + 1][0]) / 2, valley))
    outline += [(wt, bh), (wb, 0)]
    shapes = [Polygon(outline)]
    shapes += [Point(x, zt + 1.4).buffer(1.9, resolution=16) for x, zt in tips]
    zc = tips[2][1] + 2.6                   # cross on the centre ball
    shapes += [box(-0.95, zc, 0.95, zc + 4.2), box(-2.2, zc + 1.9, 2.2, zc + 3.6)]
    sil = unary_union(shapes)
    sil = sil.buffer(0.3, resolution=8).buffer(-0.6, resolution=8).buffer(0.3, resolution=8)   # soften edges
    upper = _xz_extrude(sil, CROWN_POINT_T)
    band2d = Polygon([(-wb, 0), (wb, 0), (wt, bh), (-wt, bh)])
    band = _xz_extrude(rounded(band2d, 0.6), CROWN_BAND_T)
    f, T = CROWN_FOOT, CROWN_BAND_T
    foot = hull_layers([(rounded(box(-wb - f, -T / 2 - f, wb + f, T / 2 + f), 1.5), 0.0),
                        (rounded(box(-wb, -T / 2, wb, T / 2), 0.6), f)])
    # raised jewels on both faces of the band: a diamond in the middle, round stones either side
    jewels = [Polygon([(0, bh / 2 - 2.2), (1.8, bh / 2), (0, bh / 2 + 2.2), (-1.8, bh / 2)])]
    jewels += [Point(x, bh / 2).buffer(1.35, resolution=16) for x in (-8.4, 8.4)]
    jewels += [Point(x, bh / 2).buffer(0.9, resolution=12) for x in (-15.0, 15.0)]
    gem = _xz_extrude(unary_union(jewels), T + 2 * 0.6)
    return (foot + band + upper + gem).translate((cx, cy, 0))


def build_lid_crown(lid2d):
    """lid with a crown-shaped grip over its balance point. Printed crown-up."""
    c = lid2d.centroid
    return prism(lid2d, 0, LID_T) + crown_at(0.0, c.y).translate((0, 0, LID_T - 0.01))


# ---------------------------------------------------------------- text


def text_polys(s, cap_h, max_w):
    fp = FontProperties(fname=FONT_PATH)
    ref = TextPath((0, 0), "H", size=100, prop=fp)
    cap100 = ref.get_extents().height
    size = 100 * cap_h / cap100
    # same letter size on every plaque: shrink to fit the longest name
    w = max(TextPath((0, 0), n, size=size, prop=fp).get_extents().width for n in set(NAMES + [s]))
    if w > max_w:
        size *= max_w / w
    tp = TextPath((0, 0), s, size=size, prop=fp)
    loops = [np.array(p) for p in tp.to_polygons(closed_only=True) if len(p) > 2]
    sec = CrossSection([l[:-1] if np.allclose(l[0], l[-1]) else l for l in loops], FillRule.EvenOdd)
    # close hairline gaps where serifs of neighbouring letters touch (avoids pinch points), then tidy
    sec = sec.offset(0.06, JoinType.Miter).offset(-0.06, JoinType.Miter).simplify(0.02)
    if TEXT_CLEAN > 0:   # remove slivers thinner than ~2x TEXT_CLEAN that can collapse when saved as STL
        sec = sec.offset(-TEXT_CLEAN, JoinType.Round).offset(TEXT_CLEAN, JoinType.Round).simplify(TEXT_CLEAN)
    b = sec.bounds()  # (xmin, ymin, xmax, ymax)
    capb = TextPath((0, 0), "H", size=size, prop=fp).get_extents()
    # centre horizontally on bbox, vertically on the cap height
    sec = sec.translate((-(b[0] + b[2]) / 2, -(capb.y0 + capb.y1) / 2))
    return sec, size


# ---------------------------------------------------------------- plaques
# plate frame: u = width (x), v = height (up), w = thickness from back (0) to face (PLATE_T)

def groove_prism(v_end):
    """dovetail groove in plate frame, open at v=0, blind at v=v_end; cut from the back (w=0)"""
    d = RAIL_H + FIT
    poly = Polygon([(-RAIL_W / 2 - FIT, -1), (RAIL_W / 2 + FIT, -1),
                    (RAIL_W / 2 + FIT, 0), (RAIL_W / 2 + RAIL_U + FIT, d),
                    (-RAIL_W / 2 - RAIL_U - FIT, d), (-RAIL_W / 2 - FIT, 0)])
    # poly is in (u, w); extrude along v
    m = Manifold.extrude(cs(poly), v_end + 1).translate((0, 0, -1))  # (u, w, v)
    return swap_vw(m)


def swap_vw(m):
    """(u, w, v) -> (u, v, w)"""
    return m.transform(np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0]], dtype=float))


def plate_body(back_w, face_w, height):
    """plate in plate frame with optional mitred ends (back wider than face)"""
    poly = Polygon([(-back_w / 2, 0), (back_w / 2, 0), (face_w / 2, PLATE_T), (-face_w / 2, PLATE_T)])
    return swap_vw(Manifold.extrude(cs(poly), height))


def frame_ring(w, h, inset, line, r=1.5):
    outer = rounded(box(-w / 2 + inset, inset, w / 2 - inset, h - inset), r)
    inner = outer.buffer(-line)
    return outer.difference(inner)


def plaque_B(name):
    """full-width black face plate, raised letters + raised border. Plate frame."""
    back_w = 2 * XB
    face_w = 2 * (XB - PLATE_T * COT_ANG)
    h = H
    plate = plate_body(back_w, face_w, h) - groove_prism(RAIL_Z1 - 0.0 + FIT)
    txt, _ = text_polys(name, cap_h=11.0, max_w=face_w - 14)
    txt = Manifold.extrude(txt, TEXT_RAISE).translate((0, h / 2, PLATE_T))
    ring = prism(frame_ring(face_w, h, 2.0, 0.9), 0, TEXT_RAISE).translate((0, 0, PLATE_T))
    letters = txt + ring
    # seated: plate bottom at z=0 rests on rail top via groove end; groove blind end = rail top + FIT
    return plate, letters, 0.0


def plaque_C(name, inlay=None):
    """full-width black face plate (same shape as B) with flush inlaid text + border.
    Printed face down, so the face is the smooth bed side and the groove is open on top. Plate frame."""
    inlay = TEXT_INLAY_C if inlay is None else inlay
    back_w = 2 * XB
    face_w = 2 * (XB - PLATE_T * COT_ANG)
    h = H
    plate = plate_body(back_w, face_w, h) - groove_prism(RAIL_Z1 + FIT)
    txt, _ = text_polys(name, cap_h=11.0, max_w=face_w - 14)
    txt = Manifold.extrude(txt, inlay + 0.01).translate((0, h / 2, PLATE_T - inlay))
    ring = prism(frame_ring(face_w, h, 2.0, 0.9), PLATE_T - inlay, PLATE_T + 0.01)
    ink = txt + ring                       # the same shape is used twice:
    plate = plate - ink                    #   1) as the cut-out in the plate
    ink = ink.trim_by_plane((0, 0, -1), -PLATE_T)  # 2) as the fill, flush with the face
    return plate, ink, 0.0


PLAQUE_A_H = 18.0   # small plaque height (its width follows the base)


def plaque_A(name):
    """white plaque, black inlaid text + border, flush face. Plate frame."""
    w, h = PLAQUE_A_W, PLAQUE_A_H
    z0 = H - h                                  # plaque top flush with base top
    plate = rounded(box(-w / 2, 0, w / 2, h), 1.5)
    plate = prism(plate, 0, PLATE_T)
    plate = plate - groove_prism(RAIL_Z1 - z0)
    txt, _ = text_polys(name, cap_h=9.0, max_w=w - 11)
    txt = Manifold.extrude(txt, TEXT_INLAY + 0.01).translate((0, h / 2, PLATE_T - TEXT_INLAY))
    ring = prism(frame_ring(w, h, 1.6, 0.8), PLATE_T - TEXT_INLAY, PLATE_T + 0.01)
    ink = txt + ring
    plate = plate - ink
    ink = ink.trim_by_plane((0, 0, -1), -PLATE_T)  # keep flush
    return plate, ink, z0


# ---------------------------------------------------------------- frames
def plate_to_assembly(m, z0):
    # (u, v, w) -> (x=u, y=-w, z=v+z0)
    T = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, z0]], dtype=float)
    return m.transform(T)


def plate_print_faceup(m):
    return m  # back on bed, face up


def plate_print_facedown(m):
    # (u, v, w) -> (-u, v, PLATE_T - w)
    T = np.array([[-1, 0, 0, 0], [0, 1, 0, 0], [0, 0, -1, PLATE_T]], dtype=float)
    return m.transform(T)


def lid_print(m):
    # top face on the bed
    T = np.array([[1, 0, 0, 0], [0, -1, 0, 0], [0, 0, -1, LID_T]], dtype=float)
    return m.transform(T)


# ---------------------------------------------------------------- 3MF with parts
def write_3mf_parts(path, parts):
    """parts: list of (name, trimesh, extruder). One object made of several parts, so
    Bambu Studio / OrcaSlicer / PrusaSlicer load it as a single multi-part object."""
    objs, comps = [], []
    for i, (nm, tm, _) in enumerate(parts, start=1):
        v = "".join(f'<vertex x="{a:.4f}" y="{b:.4f}" z="{c:.4f}"/>' for a, b, c in tm.vertices)
        t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tm.faces)
        objs.append(f'<object id="{i}" name="{nm}" type="model"><mesh><vertices>{v}</vertices>'
                    f'<triangles>{t}</triangles></mesh></object>')
        comps.append(f'<component objectid="{i}"/>')
    oid = len(parts) + 1
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<resources>' + "".join(objs) +
             f'<object id="{oid}" name="{os.path.basename(path)[:-4]}" type="model"><components>{"".join(comps)}</components></object>'
             f'</resources><build><item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 128 128 0"/></build></model>')
    ct = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "plaque"


def center_xy(tms):
    allv = np.vstack([t.vertices for t in tms])
    off = -(allv.min(0) + allv.max(0)) / 2
    off[2] = -allv[:, 2].min()
    for t in tms:
        t.apply_translation(off)
    return tms


def _stl_roundtrip_ok(tm):
    """True if the mesh is still watertight after saving as STL (float32) and loading again"""
    import io
    buf = io.BytesIO(tm.export(file_type="stl"))
    return trimesh.load(buf, file_type="stl").is_watertight


def export_plaque(fn, printer, name, folder, pcol, icol):
    global TEXT_CLEAN
    for clean in (0.0, 0.03, 0.06, 0.1):
        TEXT_CLEAN = clean
        plate, ink, _ = fn(name)
        p = to_trimesh(printer(plate)); i = to_trimesh(printer(ink))
        if _stl_roundtrip_ok(p) and _stl_roundtrip_ok(i):
            break
    else:
        print(f"WARNING: '{name}' still has a non-manifold edge; slicers usually repair this automatically.")
    TEXT_CLEAN = 0.0
    center_xy([p, i])
    os.makedirs(folder, exist_ok=True)
    s = slug(name)
    write_3mf_parts(os.path.join(folder, f"{s}.3mf"), [(f"plate ({pcol})", p, 1), (f"text ({icol})", i, 2)])
    p.export(os.path.join(folder, f"{s}_plate-{pcol}.stl"))
    i.export(os.path.join(folder, f"{s}_text-{icol}.stl"))
    return p, i


def main():
    global NAMES, OUT
    ap = argparse.ArgumentParser(description="Board game player screen holder generator")
    ap.add_argument("config", nargs="?", help="game config file, e.g. games/kings-dilemma.toml "
                                              "(default: the settings built into this script)")
    ap.add_argument("--names", nargs="+", help="plaque texts, one per player (overrides any names in the config)")
    ap.add_argument("--out", help="output folder (default: out/<config name>)")
    ap.add_argument("--plaques-only", action="store_true", help="only generate the name plaques")
    ap.add_argument("--list-settings", action="store_true", help="list the settings allowed in [advanced] and exit")
    ap.add_argument("--no-projects", action="store_true", help="skip the ready-to-print Bambu Studio projects")
    a = ap.parse_args()
    if a.list_settings:
        for k in tunables():
            print(f"{k} = {globals()[k]}")
        return
    sub = "default"
    if a.config:
        cfg = load_config(a.config)
        sub = os.path.splitext(os.path.basename(a.config))[0]
        print(f"config: {cfg.get('name', sub)}")
    if a.names:
        NAMES = a.names
    OUT = os.path.abspath(a.out) if a.out else os.path.join(HERE, "out", sub)
    os.makedirs(OUT, exist_ok=True)
    if not os.path.exists(FONT_PATH):
        sys.exit(f"Font not found: {FONT_PATH}")
    wing_y = YS + SCREEN_WING * S_ANG
    if wing_y > D - 3:
        print(f"WARNING: the screen wings reach y={wing_y:.1f} mm but the base is only D={D} mm deep - raise depth.")

    if not a.plaques_only:
        print("bases ...")
        to_trimesh(build_base()).export(os.path.join(OUT, "base-closed-front.stl"))
        to_trimesh(build_base_open()).export(os.path.join(OUT, "base-open-front.stl"))
        to_trimesh(build_base(floor=False)).export(os.path.join(OUT, "base-no-floor.stl"))
        print("lids (closed-front base) ...")
        lid2d = recess.buffer(-LID_CLEAR)
        to_trimesh(lid_print(build_lid())).export(os.path.join(OUT, "lid-plain.stl"))
        to_trimesh(build_lid_crown(lid2d)).export(os.path.join(OUT, "lid-crown.stl"))
        to_trimesh(build_lid_handle(lid2d)).export(os.path.join(OUT, "lid-handle.stl"))

    print("plaques ...")
    face_down = []
    for name in NAMES:
        face_down.append((name,) + export_plaque(plaque_C, plate_print_facedown, name,
                                                 os.path.join(OUT, "plaques"), "black", "white"))
        export_plaque(plaque_B, plate_print_faceup, name, os.path.join(OUT, "plaques-raised"), "black", "letters")
        export_plaque(plaque_A, plate_print_facedown, name, os.path.join(OUT, "plaques-small-white"), "white", "black")

    if not a.no_projects:
        import bambu
        proj = os.path.join(OUT, "bambu-studio")
        os.makedirs(proj, exist_ok=True)
        print("Bambu Studio projects ...")
        if not a.plaques_only:
            changed = bambu.from_template("bases.3mf", os.path.join(proj, "bases.3mf"), {
                "Open Front": to_trimesh(build_base_open()), "Closed Front": to_trimesh(build_base())})
            if changed:
                print("  bases: new geometry, so the adaptive layer heights were left out (0.2 mm layers)")
            lid2d = recess.buffer(-LID_CLEAR)
            bambu.from_template("lids.3mf", os.path.join(proj, "lids.3mf"), {
                "Crown": to_trimesh(build_lid_crown(lid2d)), "No Handle": to_trimesh(lid_print(build_lid()))})
        for p in bambu.name_plates("name-plates.3mf", proj, face_down):
            print("  " + os.path.relpath(p, OUT))
    print("done ->", OUT)


_derive()   # geometry for the built-in defaults, so the module also works when imported

if __name__ == "__main__":
    main()
