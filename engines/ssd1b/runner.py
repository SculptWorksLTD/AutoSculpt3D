# AutoSculpt 3D - (c) 2026 Enrico Migliorati. All rights reserved.
import argparse
import json
import os
import sys
import time
STYLE = '{subject}. A single complete object, whole body fully visible, centered, three-quarter view, isolated on a plain white background, soft even studio lighting, sharp focus, detailed realistic sculpture reference'
NEGATIVE = 'cropped, cut off, out of frame, multiple objects, duplicate, text, letters, watermark, logo, frame, border, busy background, scenery, floor, cast shadow, blurry, low quality, deformed'
TRANSLATORS = {'it': 'Helsinki-NLP/opus-mt-it-en', 'de': 'Helsinki-NLP/opus-mt-de-en', 'fr': 'Helsinki-NLP/opus-mt-fr-en', 'es': 'Helsinki-NLP/opus-mt-es-en', 'pt': 'Helsinki-NLP/opus-mt-ROMANCE-en', 'ja': 'Helsinki-NLP/opus-mt-ja-en'}

def say(kind, *parts, **values):
    line = ' '.join([kind, *[str(p) for p in parts]])
    if values:
        line += '\t' + json.dumps(values)
    print('\n' + line, flush=True)

def to_english(text, lang):
    repo = TRANSLATORS.get(lang)
    if not repo:
        return text
    say('PROGRESS', '0.04', 'Translating the description into English (the first time it downloads a small model)')
    try:
        from transformers import MarianMTModel, MarianTokenizer
        tok = MarianTokenizer.from_pretrained(repo)
        model = MarianMTModel.from_pretrained(repo)
        batch = tok([text], return_tensors='pt', truncation=True)
        out = model.generate(**batch, max_new_tokens=200)
        en = tok.decode(out[0], skip_special_tokens=True).strip()
        print('translated:', en, flush=True)
        return en or text
    except Exception as e:
        print('translation failed:', repr(e), flush=True)
        return text

def total_ram_gb():
    try:
        if os.name == 'nt':
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong), ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong), ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong), ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong), ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 2 ** 30
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2 ** 30
    except Exception:
        return 16

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
    ap.add_argument('--prompt-file', required=True)
    ap.add_argument('--lang', default='en')
    ap.add_argument('--output-dir', required=True)
    ap.add_argument('--weights', required=True)
    ap.add_argument('--src', default='')
    ap.add_argument('--work', default=None)
    ap.add_argument('--count', type=int, default=4)
    ap.add_argument('--quality', default='normal')
    ap.add_argument('--seed', type=int, default=-1)
    ap.add_argument('--device', default='auto')
    ap.add_argument('--precision', default='auto', help='auto | fp32 (used when half precision fails on a graphics chip)')
    a = ap.parse_args()
    os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')
    say('PROGRESS', '0.02', 'Starting the image generator')
    import torch
    subject = open(a.prompt_file, encoding='utf-8').read().strip()
    if not subject:
        raise RuntimeError('Write a description of the subject')
    subject = to_english(subject, a.lang)
    device = a.device if a.device != 'auto' else 'cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
    steps, size = {'draft': (18, 768), 'normal': (28, 1024), 'high': (40, 1024)}.get(a.quality, (28, 1024))
    say('PROGRESS', '0.08', 'Loading the image generator')
    warn_if_memory_low(7)
    from diffusers import StableDiffusionXLPipeline
    ram = total_ram_gb()
    if device == 'cpu' and ram < 12:
        say('ERROR', 'This computer has {gb} GB of memory: the pictures on this computer need at least 12 GB (or an Apple chip or an NVIDIA card). Use the online pictures instead.', gb=round(ram))
        sys.exit(1)
    marker = os.path.join(os.path.dirname(os.path.abspath(a.weights)), 'precision-fp32')
    if device == 'mps' and os.path.exists(marker) and open(marker, errors='replace').read().startswith('autocast:'):
        a.precision = 'fp32'
    dtype = torch.float16 if device in ('cuda', 'mps') and a.precision != 'fp32' else torch.float32
    if device == 'mps' and dtype == torch.float32:
        size = min(size, 768)
    elif device == 'mps' and a.quality != 'high':
        size = min(size, 896)

    def load(dt):
        print(f'device {device}, {ram:.0f} GB RAM, {dt}', flush=True)
        p = StableDiffusionXLPipeline.from_pretrained(a.weights, torch_dtype=dt, variant='fp16', use_safetensors=True).to(device)
        try:
            p.enable_attention_slicing()
        except Exception:
            pass
        if device == 'mps':
            p.vae.to(torch.float32)
        try:
            p.vae.enable_tiling()
            p.vae.enable_slicing()
        except Exception:
            pass
        return p
    pipe = load(dtype)

    def decode(latents):
        vae = pipe.vae
        lat = latents.to(device=device, dtype=torch.float32)
        mean, std = (getattr(vae.config, 'latents_mean', None), getattr(vae.config, 'latents_std', None))
        if mean is not None and std is not None:
            m = torch.tensor(mean).view(1, -1, 1, 1).to(lat)
            sd = torch.tensor(std).view(1, -1, 1, 1).to(lat)
            lat = lat * sd / vae.config.scaling_factor + m
        else:
            lat = lat / vae.config.scaling_factor
        with torch.no_grad():
            out = vae.decode(lat, return_dict=False)[0]
        return pipe.image_processor.postprocess(out, output_type='pil')[0]
    seed = a.seed if a.seed >= 0 else int(time.time()) % 1000000
    os.makedirs(a.output_dir, exist_ok=True)
    t0, n = (time.time(), max(1, min(a.count, 6)))
    for i in range(n):
        base = 0.12 + 0.85 * i / n

        def step(p, k, t, kw, i=i, base=base):
            done = i * steps + k + 1
            left = (time.time() - t0) / done * (n * steps - done)
            say('PROGRESS', round(base + 0.85 / n * (k + 1) / steps, 3), 'Picture {i} of {n}, about {m} min left', i=i + 1, n=n, m=max(1, round(left / 60)))
            return kw
        gen = torch.Generator(device='cpu').manual_seed(seed + i)
        import contextlib
        ctx = torch.autocast(device_type='cpu', dtype=torch.bfloat16) if dtype == torch.bfloat16 else contextlib.nullcontext()
        if device == 'mps' and dtype == torch.float16:
            try:
                ctx = torch.autocast(device_type='mps', dtype=torch.float16)
            except Exception:
                ctx = contextlib.nullcontext()
        args = dict(prompt=STYLE.format(subject=subject), negative_prompt=NEGATIVE, num_inference_steps=steps, guidance_scale=7.0, width=size, height=size, generator=gen, callback_on_step_end=step)
        try:
            with ctx, torch.no_grad():
                if device == 'mps':
                    img = decode(pipe(output_type='latent', **args).images)
                else:
                    img = pipe(**args).images[0]
        except RuntimeError as exc:
            if 'dtype' not in str(exc) or dtype == torch.float32:
                raise
            print('half precision failed, starting again in float32:', exc, flush=True)
            try:
                import traceback
                traceback.print_exc()
                open(marker, 'w').write('autocast: ' + str(exc)[:500])
            except Exception:
                pass
            say('PROGRESS', '0.08', 'Loading the image generator')
            sys.stdout.flush()
            os.execv(sys.executable, [sys.executable] + sys.argv + ['--precision', 'fp32'])
        img.save(os.path.join(a.output_dir, f'image{i + 1}.png'))
        say('IMAGE', os.path.join(a.output_dir, f'image{i + 1}.png'))
    say('PROGRESS', '1', 'Done in {s} seconds', s=round(time.time() - t0))
    say('RESULT', a.output_dir)
if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        say('ERROR', str(exc).replace('\n', ' ')[:400])
        sys.exit(1)
