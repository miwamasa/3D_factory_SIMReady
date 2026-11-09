"""Fetch and verify the two NVIDIA SimReady geometry packages used by the model."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from urllib.parse import quote


REVISION = '653d91e182011f357cf1644013cc94ddf194d349'
BASE = f'https://huggingface.co/datasets/nvidia/simready-assets/resolve/{REVISION}/'
ASSETS = ('conveyor_belt_a08', 'crate_blue_a01')
BOM = '.metadata/com.nvidia.simready.packaging.bom.json'


def read_asset(asset, path):
    url = BASE + quote(f'{asset}/{path}', safe='/')
    return subprocess.check_output(['curl', '-fsSL', '--retry', '1', '--max-time', '35', url])


def verified_file(asset, folder, row):
    path = row['relative_path']
    destination = folder / path
    expected = row['hash']['sha256']
    if destination.is_file() and sha256(destination.read_bytes()).hexdigest() == expected:
        return
    content = read_asset(asset, path)
    if sha256(content).hexdigest() != expected:
        raise ValueError(f'SHA-256 mismatch: {asset}/{path}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def download(asset, root):
    folder = root / asset
    folder.mkdir(parents=True, exist_ok=True)
    package = json.loads(read_asset(asset, 'com.nvidia.simready.packaging.json'))
    if package['license'] != 'CC-BY-4.0':
        raise ValueError(f'Unexpected license: {asset}')
    bom_bytes = read_asset(asset, BOM)
    if sha256(bom_bytes).hexdigest() != package['metadata'][0]['hash']['sha256']:
        raise ValueError(f'BOM hash mismatch: {asset}')
    rows = [row for row in json.loads(bom_bytes)['items']
            if row['relative_path'].startswith('simready_usd/')
            and row['relative_path'].endswith(('.usd', '.usda', '.usdc'))]
    with ThreadPoolExecutor(max_workers=5) as workers:
        list(workers.map(lambda row: verified_file(asset, folder, row), rows))
    metadata = folder / '.metadata'
    metadata.mkdir(exist_ok=True)
    (folder / BOM).write_bytes(bom_bytes)
    (metadata / 'LICENSE.txt').write_bytes(read_asset(asset, '.metadata/LICENSE.txt'))
    print(f'{asset}: {len(rows)} USD files verified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    for asset in ASSETS:
        download(asset, args.destination)


if __name__ == '__main__':
    main()
