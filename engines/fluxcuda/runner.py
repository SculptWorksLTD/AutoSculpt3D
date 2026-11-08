# AutoSculpt 3D - (c) 2026 Enrico Migliorati. All rights reserved.
import argparse
import json
import os
import sys
import time
STYLE = '{subject}. One single complete subject, the whole body fully visible and centered, seen in three-quarter view, isolated on a plain pure white background, with no pedestal, base or stand, soft even studio lighting, sharp focus, realistic, anatomically correct. A clean reference picture for a sculpture.'
STYLE_SYM = '{subject}, seen from the front and facing the camera, in a symmetrical pose with the head straight and the left and right sides alike. One single complete subject, the whole body fully visible and centered, isolated on a plain pure white background with no pedestal, base or stand, soft even studio lighting, sharp focus, realistic, anatomically correct. A clean reference picture for a sculpture.'
QUADRUPEDS = ('horse', 'pony', 'foal', 'donkey', 'mule', 'zebra', 'dog', 'puppy', 'cat', 'kitten', 'lion', 'tiger', 'leopard', 'cheetah', 'wolf', 'fox', 'bear', 'cow', 'bull', 'ox', 'calf', 'deer', 'stag', 'elk', 'moose', 'goat', 'sheep', 'ram', 'lamb', 'pig', 'boar', 'elephant', 'rhino', 'hippo', 'giraffe', 'camel', 'llama', 'alpaca', 'rabbit', 'hare', 'squirrel', 'mouse', 'rat', 'panther', 'jaguar', 'hyena', 'kangaroo', 'unicorn', 'dragon', 'dinosaur', 'triceratops', 'rhinoceros', 'hippopotamus')

def is_quadruped(subject):
    import re
    words = set(re.findall('[a-z]+', subject.lower()))
    return any((w in words or w + 's' in words for w in QUADRUPEDS))

def is_quadruped(subject):
    import re
    words = set(re.findall('[a-z]+', subject.lower()))
    return any((w in words or w + 's' in words for w in QUADRUPEDS))
TRANSLATORS = {'it': 'Helsinki-NLP/opus-mt-it-en', 'de': 'Helsinki-NLP/opus-mt-de-en', 'fr': 'Helsinki-NLP/opus-mt-fr-en', 'es': 'Helsinki-NLP/opus-mt-es-en', 'pt': 'Helsinki-NLP/opus-mt-ROMANCE-en', 'ja': 'Helsinki-NLP/opus-mt-ja-en'}

def implied_negatives(subject):
    import re
    t = subject.lower()
    out = []
    for m in re.finditer('\\b(mouth|eyes|wings|legs|paws)\\s+(?:is\\s+|are\\s+)?(?:closed|shut|folded)|\\b(?:closed|shut|folded)\\s+(mouth|eyes|wings)', t):
        w = m.group(1) or m.group(2)
        out.append({'mouth': 'open mouth, tongue out, teeth', 'eyes': 'open eyes'}.get(w, 'open ' + w + ', spread ' + w))
    for m in re.finditer('\\bwithout (?:a |an |the )?([a-z ]{3,30}?)(?:[,.]|$| and )', t):
        out.append(m.group(1).strip())
    return ', ' + ', '.join(out) if out else ''

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
    ap.add_argument('--symmetric', default='0')
    ap.add_argument('--seed', type=int, default=-1)
    a, _ = ap.parse_known_args()
    t0 = time.time()
    tiny = os.environ.get('AUTOSCULPT_TEST_TINY') == '1'
    os.makedirs(a.output_dir, exist_ok=True)
    say('PROGRESS', '0.02', 'Starting the image generator')
    prompt = open(a.prompt_file, encoding='utf-8').read().strip()
    subject = to_english(prompt, a.lang)
    import gc
    import torch
    cuda = torch.cuda.is_available()
    if not cuda and (not tiny):
        say('ERROR', 'This image generator needs an NVIDIA graphics card. Use the online pictures instead.')
        sys.exit(2)
    if total_ram_gb() < 15 and (not tiny):
        say('ERROR', 'This computer has {gb} GB of memory: the pictures on this computer need at least 16 GB. Use the online pictures instead.', gb=round(total_ram_gb()))
        sys.exit(2)
    base = os.path.join(a.weights, 'base')
    gguf = os.path.join(a.weights, 'gguf', 'flux1-schnell-Q4_K_S.gguf')
    dtype = torch.bfloat16
    text = (STYLE_SYM if a.symmetric == '1' else STYLE).format(subject=subject.rstrip('. '))
    if is_quadruped(subject):
        text += ' It has exactly four legs, each clearly visible and separate.'
        if a.symmetric == '1':
            text = text.replace('seen from the front and facing the camera', 'seen from the front, turned slightly to the side')
    print('prompt:', text, flush=True)
    say('PROGRESS', '0.05', 'Reading the description')
    from diffusers import FluxPipeline
    enc = FluxPipeline.from_pretrained(base, transformer=None, vae=None, torch_dtype=dtype)
    with torch.no_grad():
        pe, ppe, _ = enc.encode_prompt(prompt=text, prompt_2=text, device='cpu', num_images_per_prompt=1, max_sequence_length=256)
    del enc
    gc.collect()
    say('PROGRESS', '0.08', 'Loading the image generator')
    from diffusers import FluxTransformer2DModel, GGUFQuantizationConfig
    tr = FluxTransformer2DModel.from_single_file(gguf, quantization_config=GGUFQuantizationConfig(compute_dtype=dtype), torch_dtype=dtype, config=base, subfolder='transformer')
    pipe = FluxPipeline.from_pretrained(base, transformer=tr, text_encoder=None, text_encoder_2=None, tokenizer=None, tokenizer_2=None, torch_dtype=dtype)
    dev = 'cuda' if cuda else 'cpu'
    if cuda:
        vram = torch.cuda.get_device_properties(0).total_memory / 2 ** 30
        print(f'graphics card memory {vram:.1f} GB', flush=True)
        if vram >= 11:
            pipe.to('cuda')
        else:
            pipe.enable_model_cpu_offload()
        pipe.vae.enable_tiling()
    size = {'high': 1024, 'draft': 512}.get(a.quality, 768)
    steps = 2 if a.quality == 'draft' else 4
    if tiny:
        size, steps = (256, 1)
    n = max(1, min(8, a.count))
    seed0 = a.seed if a.seed >= 0 else int(time.time()) % 100000
    pe, ppe = (pe.to(dev), ppe.to(dev))
    t1 = time.time()
    for i in range(n):

        def step(p, k, t, kw, i=i):
            if cuda:
                torch.cuda.synchronize()
            done = i + (k + 1) / steps
            left = (time.time() - t1) / done * (n - done)
            say('PROGRESS', round(0.1 + 0.88 * done / n, 3), 'Picture {i} of {n}, about {m} min left', i=i + 1, n=n, m=max(1, round(left / 60)))
            return kw
        g = torch.Generator('cpu').manual_seed(seed0 + i * 7919)
        with torch.no_grad():
            img = pipe(prompt_embeds=pe, pooled_prompt_embeds=ppe, width=size, height=size, num_inference_steps=steps, guidance_scale=0.0, generator=g, callback_on_step_end=step).images[0]
        path = os.path.join(a.output_dir, f'image{i + 1}.png')
        img.save(path)
        say('IMAGE', path)
    say('PROGRESS', '1', 'Done in {s} seconds', s=round(time.time() - t0))
    say('RESULT', a.output_dir)
if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        say('ERROR', str(exc).replace('\n', ' ')[:400])
        sys.exit(1)
