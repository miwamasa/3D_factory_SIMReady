"""Build the editable Blender scene and its web GLB from the pinned SimReady USDs."""
import argparse
from math import cos, pi, sin
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector


ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--assets-dir', required=True, type=Path)
parser.add_argument('--render', type=Path)
options = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.name = 'M-100 | Motor Factory Lab'
scene.unit_settings.system = 'METRIC'
scene['simready_credit'] = 'NVIDIA SimReady Assets (CC BY 4.0), conveyor_belt_a08 and crate_blue_a01; adapted for Motor Factory Lab.'
scene['simready_source'] = 'https://huggingface.co/datasets/nvidia/simready-assets/tree/653d91e182011f357cf1644013cc94ddf194d349'


def collection(name):
    group = bpy.data.collections.new(name)
    scene.collection.children.link(group)
    return group


def material(name, hex_color, metal=0.0, roughness=0.5, emission=0.0):
    rgba = tuple((int(hex_color[i:i + 2], 16) / 255) ** 2.2 for i in (0, 2, 4)) + (1.0,)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = rgba
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = rgba
    bsdf.inputs['Metallic'].default_value = metal
    bsdf.inputs['Roughness'].default_value = roughness
    if emission:
        bsdf.inputs['Emission Color'].default_value = rgba
        bsdf.inputs['Emission Strength'].default_value = emission
    return mat


paint = material('Ice blue powder coat', '6395A7', 0.15)
cream = material('Warm grey enclosure', 'C3CDCB', 0.12)
navy = material('Graphite steel', '283944', 0.25)
steel = material('Brushed steel', 'A9C0C9', 0.56, 0.34)
rubber = material('Belt / graphite polymer', '22282D', 0.02, 0.82)
copper = material('Copper winding', 'C67843', 0.52, 0.34)
yellow = material('Machine guarding', 'EAB446', 0.10)
cyan = material('Indicator / cyan', '63DAEC', emission=0.35)
green = material('Line permissive', '7BDBAA', emission=0.65)
white = material('Wayfinding / white', 'DDE9E6')
floor = material('Light concrete', '819293', roughness=0.9)
matline = material('Bay lines', '495E64', roughness=0.8)
lane = material('Pedestrian aisle', '53696F', roughness=0.9)
crate_blue = material('SimReady crate / blue polymer', '3C7899', roughness=0.63)


GEOMETRY = {}


def mesh_for(kind, mat):
    key = kind, mat.name
    if key in GEOMETRY:
        return GEOMETRY[key]
    if kind == 'box':
        vertices = [(x, y, z) for z in (-0.5, 0.5) for y in (-0.5, 0.5) for x in (-0.5, 0.5)]
        faces = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (1, 3, 7, 5), (3, 2, 6, 7), (2, 0, 4, 6)]
    else:
        count = 24
        ring = [(0.5 * cos(i * 2 * pi / count), 0.5 * sin(i * 2 * pi / count)) for i in range(count)]
        vertices = [(x, y, z) for z in (-0.5, 0.5) for x, y in ring]
        faces = [tuple(reversed(range(count))), tuple(range(count, 2 * count))]
        faces.extend((i, (i + 1) % count, (i + 1) % count + count, i + count) for i in range(count))
    mesh = bpy.data.meshes.new(f'{kind} / {mat.name}')
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(mat)
    mesh.update()
    if kind != 'box':
        for poly in list(mesh.polygons)[2:]:
            poly.use_smooth = True
    GEOMETRY[key] = mesh
    return mesh


def make_object(name, mesh, position, scale, group, parent=None):
    obj = bpy.data.objects.new(name, mesh)
    group.objects.link(obj)
    obj.parent = parent
    obj.location = position
    obj.scale = scale
    return obj


def box(name, position, size, mat, group, parent=None):
    return make_object(name, mesh_for('box', mat), position, size, group, parent)


def cylinder(name, position, diameter, length, mat, group, parent=None, axis='Z'):
    obj = make_object(name, mesh_for('cylinder', mat), position, (diameter, diameter, length), group, parent)
    if axis == 'Y':
        obj.rotation_euler.x = pi / 2
    if axis == 'X':
        obj.rotation_euler.y = pi / 2
    return obj


def link_bar(name, a, b, diameter, mat, group, parent):
    start, finish = Vector(a), Vector(b)
    obj = cylinder(name, (start + finish) / 2, diameter, (finish - start).length, mat, group, parent)
    obj.rotation_mode = 'QUATERNION'
    obj.rotation_quaternion = (finish - start).to_track_quat('Z', 'Y')
    return obj


def pivot(name, position, group, parent):
    return make_object(name, None, position, (1, 1, 1), group, parent)


def sleeve(name, x, outer, inner, length, mat, group, parent):
    count = 32
    ring = [(cos(2 * pi * k / count), sin(2 * pi * k / count)) for k in range(count)]
    vertices = [(xx, radius * y, radius * z)
                for xx in (-length / 2, length / 2) for radius in (outer, inner)
                for y, z in ring]
    faces = []
    for a in range(count):
        b = (a + 1) % count
        faces.extend(((a, b, b + 2 * count, a + 2 * count),
                      (a + count, a + 3 * count, b + 3 * count, b + count),
                      (a, a + count, b + count, b),
                      (a + 2 * count, b + 2 * count, b + 3 * count, a + 3 * count)))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(mat)
    mesh.update()
    return make_object(name, mesh, (x, 0, 0), (1, 1, 1), group, parent)


def housing(group, parent):
    sleeve('Cast motor housing', 0, 0.295, 0.248, 0.56, paint, group, parent)
    sleeve('Flange with shaft opening', 0.30, 0.301, 0.102, 0.050, steel, group, parent)
    sleeve('Rear lip', -0.28, 0.289, 0.219, 0.053, steel, group, parent)
    box('Terminal box', (-0.08, 0, 0.305), (0.24, 0.24, 0.095), navy, group, parent)
    for k in range(14):
        angle = k * pi / 7
        fin = box('Cooling rib', (0, 0.307 * cos(angle), 0.307 * sin(angle)),
                  (0.48, 0.027, 0.064), paint, group, parent)
        fin.rotation_euler.x = angle - pi / 2


def text_label(value, position, size, mat, group, parent=None):
    font = bpy.data.curves.new(value, 'FONT')
    font.body = value
    font.size = size
    font.align_x = 'CENTER'
    font.materials.append(mat)
    obj = make_object(value, font, position, (1, 1, 1), group, parent)
    # Mesh text also survives web export.
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.convert(target='MESH')
    obj.select_set(False)
    return obj


def import_simready(asset, label, size, position, group, triangle_budget, long_x=False):
    source = next((options.assets_dir / asset / 'simready_usd').glob('sm_*.usd'))
    before = set(bpy.data.objects)
    bpy.ops.wm.usd_import(filepath=str(source), import_materials=False,
                          import_cameras=False, import_lights=False,
                          import_visible_only=True, support_scene_instancing=False)
    bpy.context.view_layer.update()
    imported = set(bpy.data.objects) - before
    meshes = [obj for obj in imported if obj.type == 'MESH' and not any(term in obj.name.lower() for term in ('collision', 'collider', 'proxy'))]
    if not meshes:
        raise RuntimeError(f'{asset} produced no visible meshes')
    count = 0
    for obj in meshes:
        obj.data.calc_loop_triangles()
        count += len(obj.data.loop_triangles)
    reduction = min(1, triangle_budget / max(1, count))
    if reduction < 1:
        for obj in meshes:
            modifier = obj.modifiers.new('Web triangle budget', 'DECIMATE')
            modifier.ratio = reduction
        bpy.context.view_layer.update()
    root = make_object(label, None, (0, 0, 0), (1, 1, 1), group)
    root['asset_source'] = f'https://huggingface.co/datasets/nvidia/simready-assets/tree/653d91e182011f357cf1644013cc94ddf194d349/{asset}'
    root['asset_credit'] = 'NVIDIA SimReady Assets, CC BY 4.0'
    children = []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in sorted(meshes, key=lambda mesh_obj: mesh_obj.name):
        mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
        for attribute in list(mesh.color_attributes):
            mesh.color_attributes.remove(attribute)
        for uv_map in list(mesh.uv_layers):
            mesh.uv_layers.remove(uv_map)
        mesh.materials.clear()
        original_name = obj.name.lower()
        if 'crate' in asset:
            mat = crate_blue
        elif any(key in original_name for key in ('rubber', 'plastic', 'cable')):
            mat = rubber
        elif any(key in original_name for key in ('roller', 'axle', 'shaft')):
            mat = steel
        else:
            mat = paint
        mesh.materials.append(mat)
        child = make_object(label + ' / ' + obj.name, mesh, (0, 0, 0), (1, 1, 1), group, root)
        child.matrix_basis = obj.matrix_world.copy()
        children.append(child)
    for obj in imported:
        bpy.data.objects.remove(obj, do_unlink=True)

    def bounds(rotation):
        points = [rotation @ child.matrix_basis @ Vector(corner) for child in children for corner in child.bound_box]
        return [min(point[i] for point in points) for i in range(3)], [max(point[i] for point in points) for i in range(3)]

    rotation = Matrix.Identity(4)
    low, high = bounds(rotation)
    if long_x and high[1] - low[1] > high[0] - low[0]:
        rotation = Matrix.Rotation(pi / 2, 4, 'Z')
        low, high = bounds(rotation)
    dimensions = [max(0.001, b - a) for a, b in zip(low, high)]
    scale = Matrix.Diagonal(Vector(tuple(goal / extent for goal, extent in zip(size, dimensions)) + (1,)))
    center = Vector(((low[0] + high[0]) / 2, (low[1] + high[1]) / 2, low[2]))
    fit = scale @ Matrix.Translation(-center) @ rotation
    for child in children:
        child.matrix_basis = fit @ child.matrix_basis
    if long_x:
        rollers = [child for child in children if 'roller' in child.name.lower()]
        if rollers:
            roller_tops = sorted(max((child.matrix_basis @ Vector(corner)).z for corner in child.bound_box) for child in rollers)
            root['deck_height'] = roller_tops[len(roller_tops) // 2]
        else:
            root['deck_height'] = size[2] * 0.76
    root.location = position
    print(f'SIMREADY {asset}: {len(children)} meshes, {count} source triangles, fitted {size}')
    return root


def duplicate_asset(original, name, location, group):
    copy = original.copy()
    copy.name = name
    copy.location = location
    group.objects.link(copy)
    for obj in original.children:
        piece = obj.copy()
        piece.parent = copy
        group.objects.link(piece)
    return copy


architecture = collection('00 | Hall, pedestrian aisles and markings')
line = collection('10 | NVIDIA SimReady conveyor and crate')
unit_group = collection('20 | M-100 workpiece for browser animation')
lights = collection('90 | Camera and lighting')
box('Concrete slab', (0, 0.45, -0.16), (24.4, 9.4, 0.32), floor, architecture)
box('Blue grey walking aisle', (0, -2.72, 0.007), (23.8, 1.32, 0.014), lane, architecture)
for side in (-3.39, -2.05):
    box('Pedestrian stripe', (0, side, 0.017), (23.8, 0.035, 0.015), yellow, architecture)
for x in range(-11, 12):
    box('Pedestrian dashed line', (x, -2.72, 0.018), (0.38, 0.035, 0.012), white, architecture)
for x in (-10.5, -7, -3.5, 0, 3.5, 7, 10.5):
    box('Cell boundary', (x, 1.07, 0.009), (0.025, 6.15, 0.013), matline, architecture)
box('Rear wall', (0, 4.92, 1.24), (24.4, 0.21, 2.48), cream, architecture)
box('Rear service stripe', (0, 4.79, 0.69), (24.3, 0.05, 0.18), paint, architecture)
for x in range(-12, 13, 3):
    box('Rear wall pillar', (x, 4.72, 1.2), (0.11, 0.27, 2.40), navy, architecture)
for x in (-10.8, -5.4, 0, 5.4, 10.8):
    box('Utility tray', (x, 4.69, 2.23), (4.5, 0.13, 0.10), steel, architecture)
    box('Wall luminaire', (x, 4.77, 1.84), (1.9, 0.10, 0.08), white, architecture)
text_label('MOTOR FACTORY LAB    /    LINE 01', (0, -3.95, 0.019), 0.42, white, architecture)

positions = [-8.75, -5.25, -1.75, 1.75, 5.25, 8.75]
belt = import_simready('conveyor_belt_a08', 'NV_Conveyor_01', (3.48, 1.28, 1.33), (positions[0], 0, 0), line, 20000, long_x=True)
for i, x in enumerate(positions[1:], 2):
    duplicate_asset(belt, f'NV_Conveyor_{i:02}', (x, 0, 0), line)
crate = import_simready('crate_blue_a01', 'NV_MaterialCrate_01', (0.88, 0.65, 0.48), (-10.65, 2.8, 0.05), line, 6500)
for i, pos in enumerate([(-10.65, 2.8, 0.52), (-9.7, 3.95, 0.05), (-8.65, 3.95, 0.05), (10.73, 2.8, 0.05)], 2):
    duplicate_asset(crate, f'NV_MaterialCrate_{i:02}', pos, line)

names = ['KIT FEED', 'WINDING', 'BEARING PRESS', 'HOUSING FIT', 'TORQUE', 'EOL TEST']
for i, (x, name) in enumerate(zip(positions, names), 1):
    sid = f'S{i:02}'
    group = collection(sid + ' | ' + name)
    root = make_object(sid, None, (x, 0, 0), (1, 1, 1), group)
    root['station_id'] = sid
    root['process'] = name
    box('Machine plinth', (0, 1.65, 0.16), (2.34, 1.7, 0.32), navy, group, root)
    box('Machine cabinet', (0, 1.68, 0.52), (2.19, 1.55, 0.64), paint, group, root)
    box('Work deck', (0, 1.64, 0.9), (2.32, 1.62, 0.13), steel, group, root)
    for xx in (-0.78, 0.02, 0.78):
        box('Service panel', (xx, 0.89, 0.54), (0.59, 0.035, 0.48), cream, group, root)
        box('Service handle', (xx, 0.863, 0.58), (0.21, 0.045, 0.048), navy, group, root)
    hmi_y = 2.46 if i == 4 else 1.37
    box('HMI pole', (1.28, hmi_y + 0.01, 0.96), (0.065, 0.065, 1.86), navy, group, root)
    box('HMI enclosure', (1.28, hmi_y, 1.94), (0.47, 0.24, 0.37), cream, group, root)
    box('HMI display', (1.28, hmi_y - 0.14, 1.95), (0.35, 0.029, 0.24), navy, group, root)
    for row in range(3):
        box('HMI status row', (1.25, hmi_y - 0.162, 1.897 + row * 0.063), (0.24 - row * 0.045, 0.012, 0.022), cyan, group, root)
    cylinder('Signal tower support', (1.04, 2.29, 2.40), 0.085, 1.10, navy, group, root)
    cylinder(sid + '_LAMP', (1.04, 2.29, 3.02), 0.15, 0.26, green, group, root)
    cylinder('Signal tower cap', (1.04, 2.29, 3.17), 0.17, 0.05, navy, group, root)
    box('Bay ID plate', (0, -1.49, 0.016), (2.40, 0.70, 0.028), navy, group, root)
    text_label(sid, (0, -1.42, 0.036), 0.30, white, group, root)
    text_label(name, (0, -1.68, 0.035), 0.127, cyan, group, root)

    fixture_y = 1.23 if i == 6 else 1.60
    root['fixture_y'] = fixture_y
    root['fixture_height'] = 1.02
    for xx in (-0.28, 0.28):
        box('Transfer rail', (xx, 0.76, 0.93), (0.075, 0.33, 0.075), steel, group, root)
    box('Pallet locating plate', (0, fixture_y, 0.988), (0.83, 0.61, 0.064), navy, group, root)

    if i == 1:
        for xx in (-0.65, 0, 0.65):
            box('Feeder tray', (xx, 2.12, 1.07), (0.54, 0.52, 0.21), cream, group, root)
            cylinder('Unprocessed motor core', (xx, 2.12, 1.25), 0.35, 0.14, steel, group, root)
        box('Feeder gantry', (0, 2.25, 2.23), (2.09, 0.20, 0.17), yellow, group, root)
        for xx in (-1.0, 1.0):
            box('Gantry post', (xx, 2.25, 1.60), (0.16, 0.15, 1.28), navy, group, root)
        head = pivot('S01_TOOL', (0, 1.60, 1.97), group, root)
        box('Sliding feeder head', (0, 0.30, 0.09), (0.33, 0.78, 0.19), paint, group, head)
        cylinder('Feeder slide', (0, 0, -0.09), 0.14, 0.30, steel, group, head)
        box('Gripper', (0, 0, -0.26), (0.28, 0.21, 0.08), navy, group, head)
    elif i in (2, 3, 5):
        for xx in (-0.96, 0.96):
            for yy in (1.02, 2.28):
                box('Machine frame upright', (xx, yy, 1.75), (0.12, 0.12, 1.65),
                    cream if i == 2 else yellow, group, root)
        box('Machine top crosshead', (0, 1.65, 2.57), (2.04, 1.4, 0.15), navy, group, root)
        if i in (2, 3):
            side = -1 if i == 2 else 1
            box('Actuator mount', (side * 0.86, 1.6, 1.22), (0.37, 0.51, 0.53), paint, group, root)
            cylinder('Actuator casing', (side * 0.77, 1.6, 1.39), 0.32, 0.40, navy, group, root, 'X')
            tool = pivot(sid + '_TOOL', (side * 0.64, 1.60, 1.39), group, root)
            cylinder('Driven spindle', (0, 0, 0), 0.14, 0.34, steel, group, tool, 'X')
            cylinder('Tool plate', (-side * 0.15, 0, 0), 0.27, 0.07, navy, group, tool, 'X')
            if i == 2:
                box('Winding head', (0.18, 0, 0.12), (0.18, 0.07, 0.13), copper, group, tool)
        else:
            box('Servo housing', (0.336, 1.60, 2.26), (0.38, 0.46, 0.46), paint, group, root)
            tool = pivot('S05_TOOL', (0.336, 1.60, 2.12), group, root)
            cylinder('Torque quill', (0, 0, 0), 0.16, 0.41, steel, group, tool)
            box('Torque socket', (0, 0, -0.22), (0.13, 0.13, 0.12), navy, group, tool)
    elif i == 4:
        box('Shell nest', (0.66, 2.12, 1.055), (0.72, 0.52, 0.18), navy, group, root)
        spare = pivot('S04_SPARE', (0.66, 2.12, 1.48), group, root)
        housing(group, spare)
        cylinder('Robot pedestal', (-0.91, 1.64, 1.20), 0.48, 0.47, navy, group, root)
        base = pivot('S04_BASE', (-0.91, 1.64, 1.49), group, root)
        shoulder = pivot('S04_SHOULDER', (0, 0, 0), group, base)
        elbow = pivot('S04_ELBOW', (1.02, 0, 0), group, shoulder)
        wrist = pivot('S04_WRIST', (0.88, 0, 0), group, elbow)
        for joint, length in ((shoulder, 1.02), (elbow, 0.88)):
            link_bar('Robot link', (0, 0, 0), (length, 0, 0), 0.19, yellow, group, joint)
            cylinder('Joint gearbox', (0, 0, 0), 0.32, 0.28, navy, group, joint, 'Y')
        box('Robot gripper', (0, 0, 0), (0.31, 0.29, 0.16), navy, group, wrist)
        for yy in (-0.14, 0.14):
            box('Gripper jaw', (0, yy, -0.11), (0.19, 0.07, 0.18), steel, group, wrist)
        # Rest pose for the standalone Blender scene.
        shoulder.rotation_euler.y = -0.96
        elbow.rotation_euler.y = 1.62
        wrist.rotation_euler.y = -0.66
    else:
        box('Test enclosure', (0, 2.10, 1.83), (1.95, 0.58, 1.59), cream, group, root)
        box('Instrument display bezel', (0, 1.796, 2.05), (1.51, 0.08, 0.66), navy, group, root)
        for k, height in enumerate((0.15, 0.21, 0.34, 0.26, 0.40, 0.33, 0.46, 0.39)):
            box(f'S06_TRACE_{k}', (-0.52 + 0.15 * k, 1.749, 1.80 + height / 2),
                (0.064, 0.024, height), cyan, group, root)
        tool = pivot('S06_TOOL', (0.67, fixture_y, 1.39), group, root)
        cylinder('Dynamometer coupling', (0, 0, 0), 0.23, 0.23, navy, group, tool, 'X')
        box('Coupling index mark', (0, 0, 0.115), (0.12, 0.055, 0.018), yellow, group, tool)

part = pivot('MOTOR_TEMPLATE', (positions[0], 0, belt['deck_height']), unit_group, None)
part['meaning'] = 'M-100 motor on a pallet, assembled from named subassemblies'
box('Pallet', (0, 0, 0.042), (0.88, 0.57, 0.079), navy, unit_group, part)
for x in (-0.22, 0.22):
    box('Pallet saddle', (x, 0, 0.132), (0.16, 0.45, 0.12), steel, unit_group, part)

stator = pivot('MOTOR_STATOR', (0, 0, 0.37), unit_group, part)
sleeve('Laminated stator', 0, 0.221, 0.135, 0.39, steel, unit_group, stator)
for x in (-0.17, -0.11, -0.05, 0.01, 0.07, 0.13):
    sleeve('Lamination edge', x, 0.225, 0.216, 0.014, navy, unit_group, stator)

winding = pivot('MOTOR_WINDING', (0, 0, 0.37), unit_group, part)
for k in range(12):
    angle = k * pi / 6
    coil = pivot(f'Winding sector {k:02}', (0, 0, 0), unit_group, winding)
    coil.rotation_euler.x = angle
    box('Copper slot', (0, 0.137, 0), (0.49, 0.039, 0.051), copper, unit_group, coil)
    for x in (-0.233, 0.233):
        box('Copper end turn', (x, 0.168, 0), (0.085, 0.078, 0.067), copper, unit_group, coil)

rotor = pivot('MOTOR_ROTOR', (0, 0, 0.37), unit_group, part)
cylinder('Rotor stack', (0, 0, 0), 0.22, 0.36, navy, unit_group, rotor, 'X')
cylinder('Ground output shaft', (0.06, 0, 0), 0.09, 0.77, steel, unit_group, rotor, 'X')
for x in (-0.245, 0.252):
    cylinder('Bearing collar', (x, 0, 0), 0.162, 0.067, steel, unit_group, rotor, 'X')

shell = pivot('MOTOR_HOUSING', (0, 0, 0.37), unit_group, part)
housing(unit_group, shell)
fasteners = pivot('MOTOR_FASTENERS', (0, 0, 0.37), unit_group, part)
for k in range(6):
    angle = pi / 2 + k * pi / 3
    cylinder(f'Flange bolt {k + 1}', (0.336, 0.257 * cos(angle), 0.257 * sin(angle)),
             0.047, 0.029, navy, unit_group, fasteners, 'X')


def light(name, pos, power, size, color):
    data = bpy.data.lights.new(name, 'AREA')
    data.energy, data.shape, data.size, data.color = power, 'DISK', size, color
    obj = make_object(name, data, pos, (1, 1, 1), lights)
    obj.rotation_euler = (Vector((0, 0, 0)) - obj.location).to_track_quat('-Z', 'Y').to_euler()


light('Key softbox', (0, -7, 13), 3600, 12, (0.88, 0.95, 1.0))
light('Rim softbox', (-5, 7, 10), 2400, 11, (0.76, 0.88, 1.0))
light('Warm end light', (12, -2, 6), 1250, 7, (1.0, 0.86, 0.67))
world = bpy.data.worlds.new('Factory ambience')
world.use_nodes = True
world.node_tree.nodes['Background'].inputs[0].default_value = (0.20, 0.25, 0.29, 1)
world.node_tree.nodes['Background'].inputs[1].default_value = 0.50
scene.world = world
camera_data = bpy.data.cameras.new('Axonometric overview')
camera_data.type = 'ORTHO'
camera_data.ortho_scale = 26.5
camera = make_object('Axonometric overview', camera_data, (9.4, -23, 15.8), (1, 1, 1), lights)
camera.rotation_euler = (Vector((0, 0.10, 0.4)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
scene.camera = camera
scene.render.engine = 'CYCLES'
scene.cycles.samples = 24
scene.cycles.use_denoising = True
scene.render.resolution_x = 1500
scene.render.resolution_y = 850
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = 'AgX'
scene.render.image_settings.file_format = 'PNG'
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            area.spaces.active.region_3d.view_perspective = 'CAMERA'
            area.spaces.active.shading.color_type = 'MATERIAL'

bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / 'models/motor_factory_line.blend'), compress=True)
bpy.ops.export_scene.gltf(filepath=str(ROOT / 'static/factory3d/line.glb'), export_format='GLB',
                          export_extras=True, export_cameras=False, export_lights=False)
if options.render:
    scene.render.filepath = str(options.render.resolve())
    bpy.ops.render.render(write_still=True)
