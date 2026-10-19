"""
Ready-to-print Bambu Studio projects (.3mf) for screen_holder.py.

The templates in templates/ are Bambu Studio projects that were actually printed (P1S, 0.4 mm nozzle, PLA Matte).
They carry all print settings: layer heights, shells, first-layer tuning, plate type, filaments, the crown's
0.12 mm height range and the bases' adaptive layer heights. Generated projects keep those settings and only swap
in the new geometry:

  bases.3mf / lids.3mf   template objects keep their plates, names and per-object settings; a mesh is only
                         replaced if the geometry changed. If a base changed, its adaptive layer-height profile is
                         dropped (it belongs to the old shape) - re-run Adaptive in Bambu Studio if you like.
  name-plates.3mf        built fresh with the template's settings: one two-part object per name (plate on
                         filament 1, text on filament 2), laid out on the plate. More names than fit on one plate
                         -> name-plates-2.3mf etc.
"""
import io, json, os, re, uuid, zipfile
import numpy as np
import trimesh

TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
_HDR = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
        'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
        'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">\n')


# ---------------------------------------------------------------- zip helpers
def _read(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def _write(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in files.items():
            z.writestr(n, data)


def _u():
    return str(uuid.uuid4())


def _fmt(x):
    return f"{x:.9g}"


def _mesh_xml(oid, tm, uid):
    v = "".join(f'     <vertex x="{_fmt(a)}" y="{_fmt(b)}" z="{_fmt(c)}"/>\n' for a, b, c in tm.vertices)
    t = "".join(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in tm.faces)
    return (f'  <object id="{oid}" p:UUID="{uid}" type="model">\n   <mesh>\n    <vertices>\n{v}    </vertices>\n'
            f'    <triangles>\n{t}    </triangles>\n   </mesh>\n  </object>\n')


def _object_file(parts):
    """parts: [(objectid, trimesh)] -> content of a 3D/Objects/object_N.model file"""
    body = "".join(_mesh_xml(oid, tm, _u()) for oid, tm in parts)
    return (_HDR + ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n <resources>\n' + body +
            ' </resources>\n <build/>\n</model>\n').encode()


def _parse_meshes(xml):
    """{objectid: trimesh} from an object file"""
    out = {}
    for oid, body in re.findall(r'<object id="(\d+)"[^>]*>(.*?)</object>', xml, re.S):
        v = np.array(re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', body), float)
        f = np.array(re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', body), int)
        if len(v):
            out[oid] = trimesh.Trimesh(v, f, process=False)
    return out


def _centered(tm):
    tm = tm.copy()
    c = (tm.bounds[0] + tm.bounds[1]) / 2
    tm.apply_translation(-c)
    return tm


def _same(a, b, tol=0.01):
    return (abs(a.volume - b.volume) < max(tol * 100, 1e-3 * abs(a.volume))
            and np.allclose(a.bounds, b.bounds, atol=tol))


def _clean_meta(model_xml):
    """drop the template designer's account id"""
    return re.sub(r'(<metadata name="DesignerUserId">)[^<]*(</metadata>)', r"\1\2", model_xml)


def bed_size(files):
    cfg = json.loads(files["Metadata/project_settings.config"])
    pts = [tuple(map(float, p.split("x"))) for p in cfg.get("printable_area", ["0x0", "256x256"])]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return max(xs) - min(xs), max(ys) - min(ys)


# ---------------------------------------------------------------- bases / lids: keep template, swap meshes
def from_template(template, out_path, meshes, drop_layer_profile_if_changed=True):
    """meshes: {substring of the template object's name: trimesh in print orientation}.
    Returns the list of object names whose geometry was replaced."""
    files = _read(os.path.join(TEMPLATES, template))
    model = files["3D/3dmodel.model"].decode()
    ms = files["Metadata/model_settings.config"].decode()
    bed_w, bed_d = bed_size(files)
    changed = []
    for m_obj in re.finditer(r'<object id="(\d+)">\s*<metadata key="name" value="([^"]*)"/>(.*?)</object>', ms, re.S):
        oid, oname, obody = m_obj.group(1), m_obj.group(2), m_obj.group(3)
        key = next((k for k in meshes if k.lower() in oname.lower()), None)
        if key is None:
            continue
        new = _centered(meshes[key])
        comp = re.search(rf'<object id="{oid}"[^>]*>\s*<components>\s*<component p:path="([^"]+)" objectid="(\d+)"[^>]*'
                         rf'transform="([^"]+)"', model)
        path, part_id, ctf = comp.group(1).lstrip("/"), comp.group(2), comp.group(3)
        old = _parse_meshes(files[path].decode())[part_id]
        ct = np.array(ctf.split(), float)[9:12]
        old_c = old.copy(); old_c.apply_translation(ct)
        if _same(_centered(old_c), new):
            continue                                   # unchanged geometry: keep the template mesh as is
        changed.append(oname)
        files[path] = _object_file([(part_id, new)])
        model = re.sub(rf'(<component p:path="/{re.escape(path)}" objectid="{part_id}"[^>]*transform=")[^"]+"',
                       r'\g<1>1 0 0 0 1 0 0 0 1 0 0 0"', model)
        # item: keep rotation and x/y, set z so the object sits on the bed
        half_h = (new.bounds[1][2] - new.bounds[0][2]) / 2

        def _z(mt):
            t = mt.group(2).split(); t[11] = _fmt(half_h)
            return mt.group(1) + " ".join(t) + '"'
        model = re.sub(rf'(<item objectid="{oid}"[^>]*transform=")([^"]+)"', _z, model)
        ms = re.sub(rf'(<assemble_item object_id="{oid}" instance_id="0" transform=")([^"]+)"', _z, ms)
        ms = re.sub(rf'(<assemble_item object_id="{oid}" volume_id="0" transform=")[^"]+"',
                    r'\g<1>1 0 0 0 1 0 0 0 1 0 0 0"', ms)
        nf = len(new.faces)
        nb = re.sub(r'<metadata face_count="\d+"/>', f'<metadata face_count="{nf}"/>', obody, 1)
        nb = re.sub(r'(<metadata key="matrix" value=")[^"]+"', r'\g<1>1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"', nb)
        nb = re.sub(r'<mesh_stat face_count="\d+"', f'<mesh_stat face_count="{nf}"', nb)
        ms = ms.replace(obody, nb, 1)
        w, d = new.extents[0], new.extents[1]
        if w > bed_w or d > bed_d:
            print(f"  WARNING: '{oname}' ({w:.0f} x {d:.0f} mm) is larger than the {bed_w:.0f} x {bed_d:.0f} mm bed")
    if changed and drop_layer_profile_if_changed:
        files.pop("Metadata/layer_heights_profile.txt", None)
    for k in [k for k in files if re.fullmatch(r"Metadata/plate_\d+\.json", k)]:
        files.pop(k)                                   # stale slicing results
    files["3D/3dmodel.model"] = _clean_meta(model).encode()
    files["Metadata/model_settings.config"] = ms.encode()
    _write(out_path, files)
    return changed


# ---------------------------------------------------------------- name plaques: fresh objects, template settings
def _layout(sizes, bed_w, bed_d, keepout=None, gap=5.0, margin=8.0):
    """shelf-pack rectangles (w, d) onto plates, as many per row as fit, avoiding the keep-out rectangle
    (x0, y0, x1, y1 in top-down bed coordinates, i.e. y measured from the back edge).
    Returns [[(index, cx, cy), ...] per plate] with cx/cy in bed coordinates."""
    def hits(x, y, w, d):
        if not keepout:
            return False
        x0, y0, x1, y1 = keepout
        return x < x1 and x + w > x0 and y < y1 and y + d > y0

    plates, cur, x, y, row_d = [], [], margin, margin, 0.0
    for i, (w, d) in enumerate(sizes):
        while True:
            if hits(x, y, w, d):
                x = keepout[2] + gap                      # jump past the prime tower
            if x + w > bed_w - margin and (cur or x > margin):
                x, y, row_d = margin, y + row_d + gap, 0.0  # next row
                continue
            if y + d > bed_d - margin and cur:
                plates.append(cur); cur, x, y, row_d = [], margin, margin, 0.0   # next plate
                continue
            break
        cur.append((i, x + w / 2, bed_d - (y + d / 2)))
        x += w + gap; row_d = max(row_d, d)
    if cur:
        plates.append(cur)
    return plates


def prime_tower_keepout(files, margin=4.0):
    """area the prime tower occupies (top-down bed coordinates), from the project's settings"""
    cfg = json.loads(files["Metadata/project_settings.config"])
    if str(cfg.get("enable_prime_tower", "0")) != "1":
        return None
    bed_w, bed_d = bed_size(files)
    first = lambda v: float(v[0] if isinstance(v, list) else v)
    tx, ty = first(cfg.get("wipe_tower_x", "165")), first(cfg.get("wipe_tower_y", "217"))
    tw = first(cfg.get("prime_tower_width", "35"))
    brim = first(cfg.get("prime_tower_brim_width", "3"))
    depth = max(tw, bed_d - ty)          # the tower's depth depends on the purge volume; assume it can reach the edge
    return (tx - brim - margin, bed_d - (ty + depth) - brim - margin, tx + tw + brim + margin, bed_d - ty + brim + margin)


def name_plates(template, out_dir, plaques, basename="name-plates"):
    """plaques: [(name, plate_trimesh, text_trimesh)] in print orientation (bed at z=0).
    Writes one project per plate-full of plaques; returns the paths."""
    tpl = _read(os.path.join(TEMPLATES, template))
    bed_w, bed_d = bed_size(tpl)
    tmodel = tpl["3D/3dmodel.model"].decode()
    tms = tpl["Metadata/model_settings.config"].decode()
    head = tmodel[:tmodel.index("<resources>")]
    plate_meta = re.search(r"<plate>(.*?)<model_instance>", tms, re.S).group(1)
    sizes = []
    for name, p, t in plaques:
        b = np.vstack([p.bounds, t.bounds])
        sizes.append((b[:, 0].max() - b[:, 0].min(), b[:, 1].max() - b[:, 1].min()))
        if sizes[-1][0] > bed_w - 10 or sizes[-1][1] > bed_d - 10:
            print(f"  WARNING: plaque '{name}' ({sizes[-1][0]:.0f} mm wide) does not fit the bed")
    paths = []
    for n, plate in enumerate(_layout(sizes, bed_w, bed_d, prime_tower_keepout(tpl)), start=1):
        files = {k: v for k, v in tpl.items() if k.startswith(("Metadata/", "[Content_Types]", "_rels/"))
                 and not k.startswith("3D/")}
        for k in [k for k in files if re.fullmatch(r"Metadata/plate_\d+\.json", k)]:
            files.pop(k)
        res, items, objs, inst, asm, rels, cut = [], [], [], [], [], [], []
        next_id = 1
        for k, (i, cx, cy) in enumerate(plate, start=1):
            name, p, t = plaques[i]
            xy = (np.vstack([p.bounds, t.bounds]).min(0) + np.vstack([p.bounds, t.bounds]).max(0)) / 2
            parts = []
            for tm in (p, t):
                m = tm.copy(); m.apply_translation([-xy[0], -xy[1], 0])
                zc = (m.bounds[0][2] + m.bounds[1][2]) / 2
                m.apply_translation([0, 0, -zc])
                parts.append((next_id, m, zc)); next_id += 1
            oid = next_id; next_id += 1
            path = f"3D/Objects/object_{k}.model"
            files[path] = _object_file([(pid, m) for pid, m, _ in parts])
            rels.append(f' <Relationship Target="/{path}" Id="rel-{k}" '
                        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n')
            comps = "".join(f'    <component p:path="/{path}" objectid="{pid}" p:UUID="{_u()}" '
                            f'transform="1 0 0 0 1 0 0 0 1 0 0 {_fmt(zc)}"/>\n' for pid, _, zc in parts)
            res.append(f'  <object id="{oid}" p:UUID="{_u()}" type="model">\n   <components>\n{comps}'
                       '   </components>\n  </object>\n')
            tf = f"1 0 0 0 1 0 0 0 1 {_fmt(cx)} {_fmt(cy)} 0"
            items.append(f'  <item objectid="{oid}" p:UUID="{_u()}" transform="{tf}" printable="1"/>\n')
            nf = sum(len(m.faces) for _, m, _ in parts)
            pxml = ""
            for j, (pid, m, zc) in enumerate(parts):
                pname = f"{name} - {'plate' if j == 0 else 'text'}"
                ext = '\n      <metadata key="extruder" value="2"/>' if j == 1 else ""
                pxml += (f'    <part id="{pid}" subtype="normal_part">\n'
                         f'      <metadata key="name" value="{_esc(pname)}"/>\n'
                         f'      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 {_fmt(zc)} 0 0 0 1"/>{ext}\n'
                         f'      <mesh_stat face_count="{len(m.faces)}" edges_fixed="0" degenerate_facets="0" '
                         'facets_removed="0" facets_reversed="0" backwards_edges="0"/>\n    </part>\n')
            objs.append(f'  <object id="{oid}">\n    <metadata key="name" value="{_esc(name)}"/>\n'
                        f'    <metadata key="extruder" value="1"/>\n    <metadata face_count="{nf}"/>\n{pxml}  </object>\n')
            inst.append(f'    <model_instance>\n      <metadata key="object_id" value="{oid}"/>\n'
                        f'      <metadata key="instance_id" value="0"/>\n'
                        f'      <metadata key="identify_id" value="{100 + k}"/>\n    </model_instance>\n')
            asm.append(f'   <assemble_item object_id="{oid}" instance_id="0" transform="{tf}" offset="0 0 0" />\n')
            cut.append(f' <object id="{k}">\n  <cut_id id="0" check_sum="1" connectors_cnt="0"/>\n </object>\n')
        files["3D/3dmodel.model"] = _clean_meta(
            head + "<resources>\n" + "".join(res) + f" </resources>\n <build p:UUID=\"{_u()}\">\n" + "".join(items) +
            " </build>\n</model>\n").encode()
        files["3D/_rels/3dmodel.model.rels"] = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns='
                                                '"http://schemas.openxmlformats.org/package/2006/relationships">\n'
                                                + "".join(rels) + "</Relationships>\n").encode()
        files["Metadata/model_settings.config"] = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n' + "".join(objs) +
                                                   "  <plate>" + plate_meta + "".join(inst) + "  </plate>\n  <assemble>\n"
                                                   + "".join(asm) + "  </assemble>\n</config>\n").encode()
        files["Metadata/cut_information.xml"] = ('<?xml version="1.0" encoding="utf-8"?>\n<objects>\n' + "".join(cut)
                                                 + "</objects>\n").encode()
        out = os.path.join(out_dir, f"{basename}.3mf" if n == 1 else f"{basename}-{n}.3mf")
        _write(out, files)
        paths.append(out)
    return paths


def _esc(s):
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
