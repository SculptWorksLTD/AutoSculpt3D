# AutoSculpt 3D - (c) 2026 Enrico Migliorati. All rights reserved.
import argparse
import json
import os
import sys
import time
import types
NEGATIVE = 'watermark, ugly, deformed, noisy, blurry, low contrast, text, cropped, multiple objects'

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

def import_mvadapter(src):
    sys.path.insert(0, src)
    import mvadapter
    for name, rel in (('mvadapter.utils', ('mvadapter', 'utils')), ('mvadapter.utils.mesh_utils', ('mvadapter', 'utils', 'mesh_utils'))):
        pkg = types.ModuleType(name)
        pkg.__path__ = [os.path.join(src, *rel)]
        sys.modules[name] = pkg
    nv = types.ModuleType('nvdiffrast')
    nv.torch = types.ModuleType('nvdiffrast.torch')
    sys.modules['nvdiffrast'], sys.modules['nvdiffrast.torch'] = (nv, nv.torch)
    import importlib
    camera = importlib.import_module('mvadapter.utils.mesh_utils.camera')
    geometry = importlib.import_module('mvadapter.utils.geometry')
    from mvadapter.pipelines.pipeline_mvadapter_i2mv_sdxl import MVAdapterI2MVSDXLPipeline
    from mvadapter.schedulers.scheduling_shift_snr import ShiftSNRScheduler
    return (MVAdapterI2MVSDXLPipeline, ShiftSNRScheduler, camera.get_orthogonal_camera, geometry.get_plucker_embeds_from_cameras_ortho)

def cut_out(img, size):
    import numpy as np
    from PIL import Image
    rgb = np.asarray(img.convert('RGB')).astype(np.float32)
    if img.mode == 'RGBA':
        alpha = np.asarray(img)[..., 3] > 10
    else:
        bg = np.median(np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]]), axis=0)
        alpha = np.abs(rgb - bg).max(axis=2) > 18
    ys, xs = np.where(alpha)
    if len(ys) < 10:
        ys, xs = (np.array([0, rgb.shape[0] - 1]), np.array([0, rgb.shape[1] - 1]))
        alpha[:] = True
    y0, y1, x0, x1 = (ys.min(), ys.max() + 1, xs.min(), xs.max() + 1)
    crop, a = (rgb[y0:y1, x0:x1], alpha[y0:y1, x0:x1].astype(np.float32)[..., None])
    crop = crop * a + 127.5 * (1 - a)
    h, w = crop.shape[:2]
    s = size * 0.9 / max(h, w)
    crop = np.asarray(Image.fromarray(crop.clip(0, 255).astype(np.uint8)).resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS))
    out = np.full((size, size, 3), 127, dtype=np.uint8)
    oy, ox = ((size - crop.shape[0]) // 2, (size - crop.shape[1]) // 2)
    out[oy:oy + crop.shape[0], ox:ox + crop.shape[1]] = crop
    return Image.fromarray(out)

def white_background(img):
    from PIL import ImageDraw
    out = img.convert('RGB')
    w, h = out.size
    step = max(1, min(w, h) // 32)
    seeds = [(x, y) for x in range(0, w, step) for y in (0, h - 1)] + [(x, y) for y in range(0, h, step) for x in (0, w - 1)]
    for xy in seeds:
        r, g, b = out.getpixel(xy)
        if max(r, g, b) - min(r, g, b) < 24 and 60 < (r + g + b) / 3 < 215:
            ImageDraw.floodfill(out, xy, (255, 255, 255), thresh=36)
    return out

def free_memory_gb():
    try:
        if sys.platform == 'darwin':
            import subprocess
            out = subprocess.run(['vm_stat'], capture_output=True, text=True, timeout=5).stdout
            page = int(out.split('page size of')[1].split()[0]) if 'page size of' in out else 16384
            pages = 0
            for key in ('Pages free', 'Pages inactive', 'Pages speculative', 'Pages purgeable'):
                for line in out.splitlines():
                    if line.startswith(key):
                        pages += int(line.split(':')[1].strip().rstrip('.'))
            return pages * page / 2 ** 30
        if os.path.exists('/proc/meminfo'):
            for line in open('/proc/meminfo'):
                if line.startswith('MemAvailable'):
                    return int(line.split()[1]) / 2 ** 20
    except Exception:
        return None
    return None

def warn_if_memory_low(need_gb):
    free = free_memory_gb()
    if free is not None and free < need_gb:
        print(f'free memory {free:.1f} GB, needed about {need_gb} GB', flush=True)
        say('PROGRESS', '0.06', 'The memory of this computer is almost full: close other programs (emulators, virtual machines, many browser tabs) to go faster')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--image', required=True)
    ap.add_argument('--prompt-file', default='')
    ap.add_argument('--lang', default='en')
    ap.add_argument('--output-dir', required=True)
    ap.add_argument('--weights', required=True)
    ap.add_argument('--src', required=True)
    ap.add_argument('--work', default=None)
    ap.add_argument('--count', type=int, default=4)
    ap.add_argument('--quality', default='normal')
    ap.add_argument('--elevation', type=float, default=0.0)
    ap.add_argument('--seed', type=int, default=-1)
    ap.add_argument('--device', default='auto')
    a = ap.parse_args()
    t0 = time.time()
    try:
        say('PROGRESS', '0.02', 'Starting the views generator')
        import torch
        from PIL import Image
        tiny = os.environ.get('AUTOSCULPT_TEST_TINY') == '1'
        device = a.device if a.device != 'auto' else 'cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
        dtype = torch.float16 if device in ('cuda', 'mps') else torch.bfloat16
        if device == 'cpu':
            try:
                ram = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2 ** 30
            except (ValueError, OSError, AttributeError):
                ram = 16
            if ram < 14 and (not tiny):
                say('ERROR', 'This computer has {gb} GB of memory: the views on this computer need at least 16 GB (or an Apple chip or an NVIDIA card).', gb=round(ram))
                sys.exit(1)
        steps, size = {'draft': (20, 768), 'normal': (30, 768), 'high': (50, 768)}.get(a.quality, (30, 768))
        if tiny:
            steps, size = (4, 512)
        elif device == 'mps':
            try:
                ram = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2 ** 30
            except (ValueError, OSError, AttributeError):
                ram = 16
            if ram < 30:
                size = 512
        print(f'device {device}, {dtype}, {steps} steps', flush=True)
        say('PROGRESS', '0.05', 'Loading the views generator')
        warn_if_memory_low(10)
        Pipe, Shift, ortho_camera, plucker = import_mvadapter(a.src)
        from diffusers import AutoencoderKL
        vae = AutoencoderKL.from_pretrained(os.path.join(a.weights, 'vae'), torch_dtype=dtype)
        pipe = Pipe.from_pretrained(os.path.join(a.weights, 'sdxl'), vae=vae, torch_dtype=dtype, variant='fp16', use_safetensors=True)
        pipe.scheduler = Shift.from_scheduler(pipe.scheduler, shift_mode='interpolated', shift_scale=8.0, scheduler_class=None)
        views = [0, 90, 180, 270]
        pipe.init_custom_adapter(num_views=len(views))
        pipe.load_custom_adapter(os.path.join(a.weights, 'adapter'), weight_name='mvadapter_i2mv_sdxl.safetensors')
        pipe.to(device=device, dtype=dtype)
        pipe.cond_encoder.to(device=device, dtype=dtype)
        pipe.enable_vae_slicing()
        cams = ortho_camera(elevation_deg=[a.elevation] * len(views), distance=[1.8] * len(views), left=-0.55, right=0.55, bottom=-0.55, top=0.55, azimuth_deg=[x - 90 for x in views], device=device)
        control = ((plucker(cams.c2w, [1.1] * len(views), size) + 1.0) / 2.0).clamp(0, 1)
        ref = cut_out(Image.open(a.image), size)
        subject = ''
        if a.prompt_file and os.path.exists(a.prompt_file):
            subject = open(a.prompt_file, encoding='utf-8').read().strip()
        text = 'high quality' if not subject or a.lang != 'en' else subject
        seed = a.seed if a.seed >= 0 else int(time.time()) % 1000000
        t1 = time.time()

        def step(p, k, t, kw):
            if device == 'mps':
                torch.mps.synchronize()
            left = (time.time() - t1) / (k + 1) * (steps - k - 1)
            say('PROGRESS', round(0.1 + 0.85 * (k + 1) / steps, 3), 'Drawing the views, about {m} min left', m=max(1, round(left / 60)))
            return kw
        with torch.no_grad():
            images = pipe(text, height=size, width=size, num_inference_steps=steps, guidance_scale=3.0, num_images_per_prompt=len(views), control_image=control, control_conditioning_scale=1.0, reference_image=ref, reference_conditioning_scale=1.0, negative_prompt=NEGATIVE, generator=torch.Generator(device='cpu').manual_seed(seed), callback_on_step_end=step).images
        os.makedirs(a.output_dir, exist_ok=True)
        for i, im in enumerate(images):
            p = os.path.join(a.output_dir, f'image{i + 1}.png')
            white_background(im).save(p)
            say('IMAGE', p)
        say('PROGRESS', '1', 'Done in {s} seconds', s=round(time.time() - t0))
        say('RESULT', a.output_dir)
    except MemoryError:
        say('ERROR', 'Not enough memory for the views: close other programs and try again')
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        say('ERROR', 'The views generator stopped: {detail}', detail=repr(e)[:300])
        sys.exit(1)
if __name__ == '__main__':
    main()
