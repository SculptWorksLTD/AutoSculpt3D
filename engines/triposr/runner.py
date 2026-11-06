# AutoSculpt 3D - (c) 2026 Enrico Migliorati. Tutti i diritti riservati.
import argparse
import os
import sys
import time
import types

def say(kind, *parts):
    print(kind, *parts, flush=True)

def install_mcubes_shim():
    try:
        import torchmcubes
        return
    except Exception:
        pass
    import numpy as np
    import torch
    from skimage.measure import marching_cubes as sk_mc

    def marching_cubes(vol, thresh):
        arr = vol.detach().float().cpu().numpy()
        verts, faces, _, _ = sk_mc(arr, level=float(thresh))
        verts = np.ascontiguousarray(verts[:, ::-1])
        faces = np.ascontiguousarray(faces[:, ::-1])
        return (torch.from_numpy(verts.astype(np.float32)), torch.from_numpy(faces.astype(np.int64)))
    mod = types.ModuleType('torchmcubes')
    mod.marching_cubes = marching_cubes
    sys.modules['torchmcubes'] = mod

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--src', required=True, help='cartella che contiene il pacchetto tsr')
    ap.add_argument('--weights', required=True, help='cartella con config.yaml e model.ckpt')
    ap.add_argument('--device', default='auto')
    ap.add_argument('--remove_bg', default='1')
    ap.add_argument('--resolution', type=int, default=256)
    ap.add_argument('--foreground', type=float, default=0.85)
    ap.add_argument('--work', default=None)
    a = ap.parse_args()
    os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')
    sys.path.insert(0, a.src)
    say('PROGRESS', '0.03', 'Avvio del motore')
    import numpy as np
    import torch
    from PIL import Image
    install_mcubes_shim()
    from tsr.system import TSR
    from tsr.utils import remove_background, resize_foreground, to_gradio_3d_orientation
    if a.device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
    else:
        device = a.device
    say('PROGRESS', '0.08', 'Preparo la foto')
    image = Image.open(a.input)
    if a.remove_bg == '1':
        say('PROGRESS', '0.10', 'Tolgo lo sfondo (la prima volta scarica un piccolo modello)')
        import rembg
        session = rembg.new_session()
        image = remove_background(image, session)
        image = resize_foreground(image, a.foreground)
        arr = np.array(image).astype(np.float32) / 255.0
        arr = arr[:, :, :3] * arr[:, :, 3:4] + (1 - arr[:, :, 3:4]) * 0.5
        image = Image.fromarray((arr * 255.0).astype(np.uint8))
    elif image.mode == 'RGBA':
        arr = np.array(image).astype(np.float32) / 255.0
        arr = arr[:, :, :3] * arr[:, :, 3:4] + (1 - arr[:, :, 3:4]) * 0.5
        image = Image.fromarray((arr * 255.0).astype(np.uint8))
    else:
        image = image.convert('RGB')
    if a.work:
        try:
            image.save(os.path.join(a.work, 'input_preparata.png'))
        except Exception:
            pass
    say('PROGRESS', '0.22', 'Carico il modello di ricostruzione')
    model = TSR.from_pretrained(a.weights, config_name='config.yaml', weight_name='model.ckpt')
    model.renderer.set_chunk_size(8192)

    def run(dev):
        model.to(dev)
        say('PROGRESS', '0.40', 'Ricostruisco la forma' + {'mps': ' (chip Apple)', 'cuda': ' (scheda video NVIDIA)'}.get(dev, ' (processore)'))
        with torch.no_grad():
            codes = model([image], device=dev)
            say('PROGRESS', '0.70', 'Estraggo la superficie')
            return model.extract_mesh(codes, False, resolution=a.resolution)
    t0 = time.time()
    try:
        meshes = run(device)
    except Exception as e:
        if device == 'cpu':
            raise
        say('PROGRESS', '0.40', 'La scheda grafica non supporta un passaggio, continuo sul processore')
        try:
            torch.mps.empty_cache() if device == 'mps' else torch.cuda.empty_cache()
        except Exception:
            pass
        meshes = run('cpu')
    say('PROGRESS', '0.92', 'Pulisco e oriento il modello')
    mesh = to_gradio_3d_orientation(meshes[0])
    try:
        parts = mesh.split(only_watertight=False)
        if len(parts) > 1:
            mesh = max(parts, key=lambda m: len(m.faces))
    except Exception:
        pass
    try:
        if mesh.volume < 0:
            mesh.invert()
    except Exception:
        pass
    os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
    mesh.export(a.output)
    say('PROGRESS', '1', 'Fatto in %d secondi' % round(time.time() - t0))
    say('RESULT', a.output)
if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        say('ERROR', str(exc).replace('\n', ' ')[:400])
        sys.exit(1)
