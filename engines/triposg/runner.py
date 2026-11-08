# AutoSculpt 3D - (c) 2026 Enrico Migliorati. All rights reserved.
import argparse
import json
import os
import sys
import time
import types

def say(kind, *parts, **values):
    line = ' '.join([kind, *[str(p) for p in parts]])
    if values:
        line += '\t' + json.dumps(values)
    print('\n' + line, flush=True)

def watch_parent():
    import threading
    parent = os.getppid()

    def run():
        while True:
            time.sleep(2)
            if os.getppid() != parent:
                os._exit(1)
    threading.Thread(target=run, daemon=True).start()
watch_parent()

def install_shims():
    if 'diso' not in sys.modules:
        try:
            import diso
        except Exception:
            mod = types.ModuleType('diso')
            mod.DiffDMC = None
            sys.modules['diso'] = mod

def patch_geometry(device):
    import torch
    import triposg.inference_utils as iu

    def dense_points(bbox_min, bbox_max, octree_depth, indexing='ij'):
        n = 2 ** octree_depth
        dt = torch.float16 if bbox_min.device.type == 'cuda' else torch.float32
        axes = [torch.linspace(float(bbox_min[i]), float(bbox_max[i]), int(n), dtype=dt, device=bbox_min.device) for i in range(3)]
        xs, ys, zs = torch.meshgrid(*axes, indexing=indexing)
        xyz = torch.stack((xs, ys, zs), dim=-1).view(-1, 3)
        return (xyz, [int(n)] * 3, bbox_max - bbox_min)
    orig_expand = iu.expand_edge_region_fast

    def expand(edge_coords, grid_size):
        if edge_coords.device.type == 'cuda':
            return orig_expand(edge_coords, grid_size)
        t = torch.zeros(grid_size, grid_size, grid_size, device=edge_coords.device, dtype=torch.float32)
        t[edge_coords[:, 0], edge_coords[:, 1], edge_coords[:, 2]] = 1
        k, p = (5, 2) if grid_size < 512 else (3, 1)
        pooled = torch.nn.functional.max_pool3d(t[None, None], kernel_size=k, stride=1, padding=p).squeeze()
        low = torch.nonzero(pooled, as_tuple=False).to(torch.int32)
        x, y, z = (low[:, 0] * 2, low[:, 1] * 2, low[:, 2] * 2)
        return torch.stack([torch.cat((x, x, x, x, x + 1, x + 1, x + 1, x + 1)), torch.cat((y, y, y + 1, y + 1, y, y, y + 1, y + 1)), torch.cat((z, z + 1, z, z + 1, z, z + 1, z, z + 1))], dim=1)
    iu.generate_dense_grid_points_gpu = dense_points
    iu.expand_edge_region_fast = expand
    if device != 'cuda':
        torch.cuda.empty_cache = lambda: None

def prepare_photo(path, remove_bg, work):
    import numpy as np
    from PIL import Image
    img = Image.open(path)
    img.load()
    if max(img.size) > 2000:
        img.thumbnail((2000, 2000))
    if img.mode == 'RGBA' and np.asarray(img)[:, :, 3].min() < 250:
        rgba = img
    elif remove_bg:
        say('PROGRESS', '0.06', 'Removing the background (the first time it downloads a small model)')
        import rembg
        try:
            session = rembg.new_session('isnet-general-use')
        except Exception:
            session = rembg.new_session()
        rgba = rembg.remove(img.convert('RGB'), session=session)
    else:
        rgba = img.convert('RGBA')
    arr = np.asarray(rgba.convert('RGBA')).astype(np.float32) / 255.0
    alpha = arr[:, :, 3]
    mask = alpha > 0.5
    if mask.sum() < 50:
        raise RuntimeError('The subject was not found in the photo: try a photo with a plain background')
    try:
        from skimage.measure import label
        lab = label(mask)
        if lab.max() > 1:
            sizes = np.bincount(lab.ravel())
            sizes[0] = 0
            keep = sizes >= max(200, sizes.max() * 0.02)
            mask = keep[lab]
            alpha = alpha * mask
    except Exception:
        pass
    try:
        from scipy import ndimage
        filled = ndimage.binary_fill_holes(mask)
        small = filled & ~mask
        lab2, n2 = ndimage.label(small)
        if n2:
            sizes = np.bincount(lab2.ravel())
            sizes[0] = 0
            fill = (sizes <= max(64, mask.sum() * 0.004))[lab2] & small
            mask = mask | fill
            alpha = np.where(fill, 1.0, alpha)
    except Exception:
        pass
    try:
        from scipy import ndimage
        alpha = np.clip(ndimage.gaussian_filter(alpha, 0.7), 0, 1) * ndimage.binary_dilation(mask, iterations=2)
    except Exception:
        pass
    rows, cols = np.where(mask)
    y0, y1, x0, x1 = (rows.min(), rows.max() + 1, cols.min(), cols.max() + 1)
    rgb = arr[:, :, :3] * alpha[:, :, None] + (1 - alpha[:, :, None])
    crop = rgb[y0:y1, x0:x1]
    h, w = crop.shape[:2]
    side = int(max(h, w) * 1.2)
    canvas = np.ones((side, side, 3), dtype=np.float32)
    oy, ox = ((side - h) // 2, (side - w) // 2)
    canvas[oy:oy + h, ox:ox + w] = crop
    out = Image.fromarray((canvas * 255).astype(np.uint8))
    if work:
        try:
            out.save(os.path.join(work, 'prepared_input.png'))
        except Exception:
            pass
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--src', required=True, help='folder containing the triposg package')
    ap.add_argument('--weights', required=True, help='folder with the TripoSG weights')
    ap.add_argument('--device', default='auto')
    ap.add_argument('--remove_bg', default='1')
    ap.add_argument('--quality', default='normal')
    ap.add_argument('--steps', type=int, default=0)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--work', default=None)
    a = ap.parse_args()
    os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')
    sys.path.insert(0, a.src)
    say('PROGRESS', '0.02', 'Starting the engine')
    import numpy as np
    import torch
    install_shims()
    if a.device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
    else:
        device = a.device
    dense, fine, steps = {'draft': (6, 7, 25), 'normal': (7, 8, 40), 'high': (8, 9, 50)}.get(a.quality, (7, 8, 40))
    if a.steps:
        steps = a.steps
    image = prepare_photo(a.input, a.remove_bg == '1', a.work)
    say('PROGRESS', '0.12', 'Loading the reconstruction model')
    from triposg.pipelines.pipeline_triposg import TripoSGPipeline
    patch_geometry(device)

    def run(dev):
        dtype = torch.float16 if dev == 'cuda' else torch.float32
        pipe = TripoSGPipeline.from_pretrained(a.weights).to(dev, dtype)
        say('PROGRESS', '0.18', {'mps': 'Rebuilding the shape (Apple chip)', 'cuda': 'Rebuilding the shape (NVIDIA graphics card)'}.get(dev, 'Rebuilding the shape (processor)'))
        t0 = time.time()

        def step(p, i, t, kw):
            if dev == 'mps':
                torch.mps.synchronize()
            done = i + 1
            left = (time.time() - t0) / done * (steps - done)
            say('PROGRESS', round(0.18 + 0.62 * done / steps, 3), 'Shape step {i} of {n}, about {m} min left', i=done, n=steps, m=max(1, round(left / 60)))
            return kw
        gen = torch.Generator(device='cpu' if dev == 'mps' else dev).manual_seed(a.seed)
        with torch.no_grad():
            pipe.vae.enable_slicing() if hasattr(pipe.vae, 'enable_slicing') else None
            out = pipe(image=image, generator=gen, num_inference_steps=steps, guidance_scale=7.0, use_flash_decoder=False, dense_octree_depth=dense, hierarchical_octree_depth=fine, callback_on_step_end=step)
        return out.samples[0]
    t_start = time.time()
    try:
        verts, faces = run(device)
    except Exception:
        if device == 'cpu':
            raise
        say('PROGRESS', '0.18', 'The graphics chip does not support a step, continuing on the processor')
        try:
            torch.mps.empty_cache() if device == 'mps' else torch.cuda.empty_cache()
        except Exception:
            pass
        verts, faces = run('cpu')
    if verts is None:
        raise RuntimeError('The model produced no surface: try another photo')
    say('PROGRESS', '0.92', 'Cleaning and orienting the model')
    import trimesh
    mesh = trimesh.Trimesh(np.asarray(verts, dtype=np.float32), np.asarray(faces), process=True)
    try:
        parts = mesh.split(only_watertight=False)
        if len(parts) > 1:
            mesh = max(parts, key=lambda m: len(m.faces))
    except Exception:
        pass
    for fix in (lambda: mesh.update_faces(mesh.nondegenerate_faces()), lambda: mesh.update_faces(mesh.unique_faces()), mesh.remove_unreferenced_vertices, lambda: trimesh.repair.fill_holes(mesh), lambda: trimesh.repair.fix_normals(mesh)):
        try:
            fix()
        except Exception:
            pass
    try:
        if mesh.volume < 0:
            mesh.invert()
    except Exception:
        pass
    os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
    mesh.export(a.output)
    say('PROGRESS', '1', 'Done in {s} seconds', s=round(time.time() - t_start))
    say('RESULT', a.output)
if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        say('ERROR', str(exc).replace('\n', ' ')[:400])
        sys.exit(1)
