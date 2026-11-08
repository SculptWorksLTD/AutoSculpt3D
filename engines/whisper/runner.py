# AutoSculpt 3D - (c) 2026 Enrico Migliorati. All rights reserved.
import argparse
import json
import os
import sys
import time
import wave

def say(kind, *parts, **values):
    line = ' '.join([kind, *[str(p) for p in parts]])
    if values:
        line += '\t' + json.dumps(values)
    print('\n' + line, flush=True)
LANGS = {'it': 'italian', 'en': 'english', 'de': 'german', 'fr': 'french', 'es': 'spanish', 'pt': 'portuguese', 'ja': 'japanese'}

def read_wav(path):
    import numpy as np
    with wave.open(path, 'rb') as w:
        rate, ch, width, n = (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes())
        raw = w.readframes(n)
    if width != 2:
        raise RuntimeError('Unsupported recording format')
    x = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != 16000:
        t = np.arange(0, len(x) / rate, 1 / 16000)
        x = np.interp(t, np.arange(len(x)) / rate, x).astype(np.float32)
    return x

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--audio', required=True)
    ap.add_argument('--lang', default='')
    ap.add_argument('--output', required=True)
    ap.add_argument('--weights', required=True)
    ap.add_argument('--src', default='')
    ap.add_argument('--work', default=None)
    a = ap.parse_args()
    os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')
    t0 = time.time()
    say('PROGRESS', '0.05', 'Listening to the recording')
    import torch
    from transformers import WhisperForConditionalGeneration, WhisperProcessor
    audio = read_wav(a.audio)
    if len(audio) < 16000 * 0.4:
        raise RuntimeError('The recording is too short: hold the button and speak')
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    proc = WhisperProcessor.from_pretrained(a.weights)
    model = WhisperForConditionalGeneration.from_pretrained(a.weights).to(device)
    say('PROGRESS', '0.35', 'Writing what you said')
    feats = proc(audio, sampling_rate=16000, return_tensors='pt').input_features.to(device)
    kw = {'task': 'transcribe'}
    if a.lang in LANGS:
        kw['language'] = LANGS[a.lang]
    with torch.no_grad():
        ids = model.generate(feats, max_new_tokens=200, **kw)
    text = proc.batch_decode(ids, skip_special_tokens=True)[0].strip()
    if not text:
        raise RuntimeError('No words were recognised: speak a little louder and closer')
    with open(a.output, 'w', encoding='utf-8') as f:
        f.write(text)
    say('PROGRESS', '1', 'Done in {s} seconds', s=round(time.time() - t0))
    say('RESULT', a.output)
if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        say('ERROR', str(exc).replace('\n', ' ')[:400])
        sys.exit(1)
