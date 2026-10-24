"""Execute inside Autodesk 3ds Max, using its bundled Python and pymxs.

This source is not evidence that a MAX/FBX export has already run.
"""
import datetime
import hashlib
import json
import math
from pathlib import Path

from pymxs import runtime as rt


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def point(value):
    # Y-up source -> right-handed Max Z-up. FBX Y-up reverses this rotation.
    return rt.Point3(float(value[0]), -float(value[2]), float(value[1]))


def material(spec):
    color = rt.Color(*[round(channel * 255) for channel in spec['color']])
    mat = rt.PhysicalMaterial()
    mat.name = spec['id']
    mat.base_color = color
    mat.metalness = float(spec['metallic'])
    mat.roughness = float(spec['roughness'])
    return mat


def create_part(part, materials):
    shape = part['shape']
    if shape == 'box':
        x, y, z = part['size']
        obj = rt.Box(width=x, length=z, height=y, widthsegs=1, lengthsegs=1, heightsegs=1)
        pos = list(part['center'])
        pos[1] -= y / 2
        obj.pos = point(pos)
        # Keep optional bevels explicit; unsupported Chamfer versions fail visibly.
        if part.get('bevel', 0):
            bevel = rt.Chamfer()
            if not rt.isProperty(bevel, rt.Name('amount')):
                raise RuntimeError('Installed Chamfer modifier lacks amount; adapt to this Max version.')
            bevel.amount = part['bevel']
            rt.addModifier(obj, bevel)
    elif shape == 'cylinder':
        start, end = point(part['start']), point(part['end'])
        direction = end - start
        obj = rt.Cylinder(radius=part['radius'], height=rt.length(direction), sides=16, heightsegs=1)
        obj.transform = rt.matrixFromNormal(rt.normalize(direction))
        obj.pos = start
    elif shape == 'torus':
        obj = rt.Torus(radius1=part['radius'], radius2=part['tube'], segments=24, sides=8)
        obj.rotation = rt.eulerAngles(0, 90, 0) if part.get('axis') == 'x' else rt.eulerAngles(90, 0, 0)
        obj.pos = point(part['center'])
    else:
        raise ValueError('Unknown source primitive: ' + shape)
    obj.name = 'Fumai_' + part['name']
    obj.material = materials[part['material']]
    rt.convertToPoly(obj)
    return obj


def build_lod(model, materials, level):
    buckets = {}
    for part in model['parts']:
        if level == 1 and part.get('detail'):
            continue
        obj = create_part(part, materials)
        key = part['material']
        if key in buckets:
            rt.polyop.attach(buckets[key], obj)
        else:
            obj.name = model['id'] + '_' + key
            buckets[key] = obj
    if not buckets:
        raise RuntimeError('Empty model')
    for obj in buckets.values():
        rt.setUserProp(obj, 'fumai_source', 'constructed_demo')
        rt.setUserProp(obj, 'fumai_dimensions', 'illustrative_not_surveyed')
    return list(buckets.values())


def main():
    if len(rt.objects):
        raise RuntimeError('Use a new empty 3ds Max scene; existing scene is never reset automatically.')
    root = Path(__file__).resolve().parents[1]
    source = root / 'source' / 'equipment-recipe.json'
    recipe = json.loads(source.read_text(encoding='utf-8'))
    if recipe['units'] != 'm' or recipe['up'] != 'Y':
        raise ValueError('Unexpected source units or axis')
    version = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = root / 'artifacts' / 'max' / version
    output.mkdir(parents=True, exist_ok=False)
    rt.units.SystemType = rt.Name('Meters')
    rt.units.SystemScale = 1.0
    rt.units.DisplayType = rt.Name('Metric')
    rt.units.MetricType = rt.Name('Meters')
    rt.pluginManager.loadClass(rt.FBXEXP)
    if not rt.FBXExporterSetParam('ResetExport'):
        raise RuntimeError('FBX exporter unavailable')
    rt.FBXExporterSetParam('UpAxis', 'Y')
    rt.FBXExporterSetParam('Animation', False)
    rt.FBXExporterSetParam('Cameras', False)
    rt.FBXExporterSetParam('Lights', False)
    rt.FBXExporterSetParam('EmbedTextures', False)
    materials = {m['id']: material(m) for m in recipe['materials']}
    receipt = dict(schema_version=1, runtime='Autodesk 3ds Max / pymxs', max_version=str(rt.maxVersion()),
                   source_kind=recipe['source'], units='m', up='Y', source_sha256=sha(source), files=[])
    for model in recipe['models']:
        for level in [0, 1]:
            objects = build_lod(model, materials, level)
            rt.select(objects)
            name = model['id'] + '_LOD' + str(level)
            fbx = output / (name + '.fbx')
            rt.exportFile(str(fbx), rt.Name('noPrompt'), selectedOnly=True, using=rt.FBXEXP)
            if not fbx.is_file() or fbx.stat().st_size < 100:
                raise RuntimeError('FBX export did not produce a model: ' + name)
            max_file = output / (name + '.max')
            rt.saveMaxFile(str(max_file), quiet=True)
            if not max_file.is_file():
                raise RuntimeError('MAX save failed: ' + name)
            receipt['files'].append(dict(model=model['id'], lod=level, fbx=fbx.name, fbx_sha256=sha(fbx),
                                         max=max_file.name, max_sha256=sha(max_file)))
            rt.delete(objects)
    (output / 'max-export.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    pointer = root / 'artifacts' / 'max' / 'latest.json'
    pointer.write_text(json.dumps({'relative_directory': version}, indent=2), encoding='utf-8')
    print('FUMAI_MAX_EXPORT_OK ' + str(output))


main()
