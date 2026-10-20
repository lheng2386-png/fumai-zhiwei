"""Single-GPU SimSiam-style thermal domain adaptation of ImageNet ConvNeXt.

Independent implementation of the stop-gradient symmetric objective described
by Chen & He (https://arxiv.org/abs/2011.10566), not the official ResNet recipe.
The prediction head is discarded; downstream selection must compare against
an unadapted ImageNet backbone using the same supervised controls.
"""
import argparse
from contextlib import nullcontext
import hashlib
import io
import json
import math
from pathlib import Path
import time

import numpy as np
from PIL import Image
import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms
from torchvision.transforms import InterpolationMode

from train_deep import seed_all
from train_pvf10_transfer import build_model


def siamese_loss(p1, p2, z1, z2):
    return -.5 * (F.cosine_similarity(p1.float(), z2.detach().float(), dim=1).mean()
                  + F.cosine_similarity(p2.float(), z1.detach().float(), dim=1).mean())


class SiameseEncoder(nn.Module):
    def __init__(self, encoder, features, projection=1024, hidden=256):
        super().__init__()
        self.encoder = encoder
        self.projector = nn.Sequential(
            nn.Linear(features, projection, bias=False), nn.BatchNorm1d(projection), nn.ReLU(inplace=True),
            nn.Linear(projection, projection, bias=False), nn.BatchNorm1d(projection), nn.ReLU(inplace=True),
            nn.Linear(projection, projection, bias=False), nn.BatchNorm1d(projection, affine=False))
        self.predictor = nn.Sequential(nn.Linear(projection, hidden, bias=False),
                                       nn.BatchNorm1d(hidden), nn.ReLU(inplace=True),
                                       nn.Linear(hidden, projection))

    def forward(self, first, second):
        z1, z2 = self.projector(self.encoder(first)), self.projector(self.encoder(second))
        return self.predictor(z1), self.predictor(z2), z1, z2


def make_views():
    # Conservative geometric/view changes; never claim these simulate radiometry.
    return transforms.Compose([
        transforms.ToPILImage(),
        transforms.RandomResizedCrop(224, scale=(.6, 1.0), ratio=(.9, 1.1),
                                      interpolation=InterpolationMode.BILINEAR),
        transforms.RandomHorizontalFlip(), transforms.ToTensor(),
        transforms.Normalize((.485, .456, .406), (.229, .224, .225))])


class PackedJPEGs:
    def __init__(self, payload, offsets, expected_size):
        if (payload.dtype != np.uint8 or payload.ndim != 1 or offsets.dtype != np.int64
                or offsets.ndim != 1 or len(offsets) < 2 or offsets[0] != 0
                or offsets[-1] != len(payload) or np.any(np.diff(offsets) <= 0)):
            raise ValueError("Invalid packed JPEG offsets/payload")
        self.payload, self.offsets, self.expected_size = payload, offsets, expected_size

    def __len__(self):
        return len(self.offsets) - 1

    def __getitem__(self, index):
        raw = self.payload[self.offsets[index]:self.offsets[index + 1]].tobytes()
        with Image.open(io.BytesIO(raw)) as image:
            if image.size != (self.expected_size, self.expected_size) or image.mode != "RGB":
                raise ValueError("Packed JPEG image format mismatch")
            return np.asarray(image).copy()


class TwoViews(Dataset):
    def __init__(self, images, transform=None):
        self.images = images
        self.transform = transform if transform is not None else make_views()

    def __len__(self):
        return len(self.images)

    def __getitem__(self, index):
        return self.transform(self.images[index]), self.transform(self.images[index])


def backbone_state(encoder):
    return {name: value.detach().cpu().clone() for name, value in encoder.state_dict().items()
            if not name.startswith('classifier.2.')}


def run(args):
    if args.output.exists() and not args.resume:
        raise FileExistsError(args.output)
    if args.resume and not (args.output / "last.pt").is_file():
        raise FileNotFoundError("Resume checkpoint missing")
    if not torch.cuda.is_available():
        raise RuntimeError('Formal pretraining requires CUDA; no automatic CPU fallback')
    if args.epochs < 1 or args.batch_size < 2 or args.workers < 0 or not math.isfinite(args.lr) or args.lr <= 0:
        raise ValueError('Invalid training controls')
    manifest_bytes = (args.data / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    cache_path = args.data / 'unlabeled.npz'
    if hashlib.sha256(cache_path.read_bytes()).hexdigest() != manifest['cache_sha256']:
        raise ValueError('Pretraining cache SHA mismatch')
    with np.load(cache_path, allow_pickle=False) as data:
        if manifest.get('image_encoding') == 'jpeg_q95_444':
            images = PackedJPEGs(data['jpeg_data'], data['jpeg_offsets'], manifest['size'])
        else:
            images = data['images']
            if images.dtype != np.uint8 or images.shape[1:] != (manifest['size'], manifest['size'], 3):
                raise ValueError('Invalid RGB cache shape or dtype')
        paths, groups = data['source_path'], data['source_component']
    if (len(images) != manifest['images'] or len(images) < args.batch_size or len(groups) != len(images)
            or len(np.unique(groups)) != len(images)
            or not all(str(p).startswith('Common_data/train/images/') for p in paths)):
        raise ValueError('Malformed or non-training pretraining cache')
    seed_all(args.seed)
    encoder, _, _ = build_model('convnext_tiny', 10)
    features = encoder.classifier[2].in_features
    encoder.classifier[2] = nn.Identity()
    model = SiameseEncoder(encoder, features).cuda()
    loader = DataLoader(TwoViews(images), batch_size=args.batch_size, shuffle=True,
                        num_workers=args.workers, drop_last=True, pin_memory=True,
                        persistent_workers=args.workers > 0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=.01)
    args.output.mkdir(parents=True, exist_ok=args.resume)
    protocol = {'method': 'SimSiam-style adaptation; ConvNeXt-Tiny, single GPU, AdamW; independent implementation',
                'reference': 'https://arxiv.org/abs/2011.10566', 'architecture': 'convnext_tiny',
                'initialization': 'torchvision ImageNet1K V1',
                'initial_weights_sha256': hashlib.sha256((Path(torch.hub.get_dir()) / 'checkpoints/convnext_tiny-983f1562.pth').read_bytes()).hexdigest(),
                'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'projection_dimensions': [features, 1024, 256],
                'source_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                'cache_sha256': manifest['cache_sha256'], 'seed': args.seed,
                'image_encoding': manifest.get('image_encoding', 'rgb_uint8_lossless_cache'),
                'images': len(images), 'epochs': args.epochs, 'batch_size': args.batch_size, 'lr': args.lr,
                'gpu': torch.cuda.get_device_name(0), 'torch': str(torch.__version__),
                'selection': 'fixed final epoch; no downstream validation or labels opened',
                'downstream_test_status': 'not opened',
                'limitations': manifest['limitations'] + [
                    'Scene-level adaptation may not transfer to component-crop diagnosis.',
                    'Loss and feature variance cannot establish downstream industrial benefit.',
                    'Worker augmentation RNG restarts on resume, so exact interrupted-trajectory equivalence is not guaranteed.',
                    'Not an official SimSiam replication or a claim of SOTA.']}
    history, first_epoch = [], 0
    if args.resume:
        saved = torch.load(args.output / "last.pt", map_location="cpu", weights_only=True)
        for key in ("cache_sha256", "source_manifest_sha256", "seed", "epochs", "batch_size", "lr"):
            if saved["protocol"][key] != protocol[key]:
                raise ValueError(f"Resume protocol mismatch: {key}")
        model.load_state_dict(saved["model"], strict=True)
        optimizer.load_state_dict(saved["optimizer"])
        torch.set_rng_state(saved["torch_rng"])
        torch.cuda.set_rng_state_all(saved["cuda_rng"])
        first_epoch, history = saved["epoch"], saved["history"]
    (args.output / 'protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
    started = time.monotonic()
    amp = torch.cuda.is_bf16_supported()
    for epoch in range(first_epoch, args.epochs):
        model.train()
        factor = .1 + .9 * (1 + math.cos(math.pi * epoch / max(args.epochs - 1, 1))) / 2
        for group in optimizer.param_groups:
            group['lr'] = args.lr * factor
        total, count, std_sum = 0., 0, 0.
        for first, second in loader:
            first, second = first.cuda(non_blocking=True), second.cuda(non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast('cuda', dtype=torch.bfloat16) if amp else nullcontext():
                p1, p2, z1, z2 = model(first, second)
                loss = siamese_loss(p1, p2, z1, z2)
            if not torch.isfinite(loss):
                raise FloatingPointError('Nonfinite pretraining loss')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.)
            optimizer.step()
            total += float(loss.detach()) * len(first)
            std_sum += float(F.normalize(z1.detach().float(), dim=1).std(dim=0, unbiased=False).mean()) * len(first)
            count += len(first)
        row = {'epoch': epoch + 1, 'loss': total / count,
               'normalized_projection_std': std_sum / count,
               'samples_used': count, 'seconds': time.monotonic() - started}
        history.append(row)
        print(json.dumps(row), flush=True)
        # Save latest resumable optimizer state separately from downstream backbone.
        torch.save({'model': model.state_dict(), 'optimizer': optimizer.state_dict(),
                    'epoch': epoch + 1, 'protocol': protocol, 'history': history,
                    'torch_rng': torch.get_rng_state(), 'cuda_rng': torch.cuda.get_rng_state_all()}, args.output / 'last.pt.tmp')
        (args.output / 'last.pt.tmp').replace(args.output / 'last.pt')
    torch.save({'architecture': 'convnext_tiny', 'pretraining_method': protocol['method'],
                'backbone_state_dict': backbone_state(model.encoder),
                'source_manifest_sha256': protocol['source_manifest_sha256'],
                'cache_sha256': manifest['cache_sha256'], 'seed': args.seed,
                'image_encoding': manifest.get('image_encoding', 'rgb_uint8_lossless_cache'),
                'epochs': args.epochs}, args.output / 'backbone.pt')
    (args.output / 'results.json').write_text(json.dumps({**protocol, 'history': history,
                                                       'status': 'complete'}, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--resume', action='store_true', help='Resume last complete epoch; worker augmentation RNG restarts')
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=20)
    parser.add_argument('--batch-size', type=int, default=64)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--seed', type=int, default=2026)
    parser.add_argument('--lr', type=float, default=1e-4)
    run(parser.parse_args())
