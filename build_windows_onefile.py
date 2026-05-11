import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw


ROOT_DIR = Path(__file__).resolve().parent
RAWLOGS_DIR = ROOT_DIR / 'rawlogs'
BUILD_ASSETS_DIR = ROOT_DIR / 'build_assets'
ICONS_DIR = BUILD_ASSETS_DIR / 'icons'
PYINSTALLER_DIR = BUILD_ASSETS_DIR / 'pyinstaller'
WORK_DIR = PYINSTALLER_DIR / 'work'
SPEC_DIR = PYINSTALLER_DIR / 'spec'


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def make_canvas(background: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((16, 16, 240, 240), radius=56, fill=background)
    draw.rounded_rectangle((28, 28, 228, 228), radius=46, outline=(255, 255, 255, 70), width=3)
    return image, draw


def save_icon(image: Image.Image, target: Path) -> None:
    ensure_dir(target.parent)
    image.save(target, format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])


def draw_uploader_icon(target: Path) -> None:
    image, draw = make_canvas('#E95420')
    draw.rounded_rectangle((62, 154, 194, 198), radius=18, fill=(255, 248, 244, 255))
    draw.rectangle((86, 120, 110, 154), fill=(255, 248, 244, 255))
    draw.polygon([(128, 58), (76, 126), (180, 126)], fill=(255, 248, 244, 255))
    draw.rounded_rectangle((74, 164, 182, 176), radius=6, fill=(233, 84, 32, 255))
    draw.rounded_rectangle((74, 182, 168, 190), radius=4, fill=(233, 84, 32, 180))
    save_icon(image, target)


def draw_splitter_icon(target: Path) -> None:
    image, draw = make_canvas('#77216F')
    draw.rounded_rectangle((46, 62, 104, 194), radius=24, fill=(255, 248, 244, 255))
    draw.rounded_rectangle((150, 62, 208, 120), radius=22, fill=(255, 248, 244, 255))
    draw.rounded_rectangle((150, 136, 208, 194), radius=22, fill=(255, 248, 244, 255))
    draw.line((102, 98, 148, 98), fill=(255, 248, 244, 255), width=14)
    draw.line((102, 158, 148, 158), fill=(255, 248, 244, 255), width=14)
    draw.polygon([(148, 98), (124, 78), (124, 118)], fill=(255, 248, 244, 255))
    draw.polygon([(148, 158), (124, 138), (124, 178)], fill=(255, 248, 244, 255))
    save_icon(image, target)


def generate_icons() -> dict[str, Path]:
    ensure_dir(ICONS_DIR)
    uploader_icon = ICONS_DIR / 'solo_pixi_uploader.ico'
    splitter_icon = ICONS_DIR / 'solo_pixi_splitter.ico'
    draw_uploader_icon(uploader_icon)
    draw_splitter_icon(splitter_icon)
    return {
        'uploader': uploader_icon,
        'splitter': splitter_icon,
    }


def run_pyinstaller(command: list[str]) -> None:
    print('Running:', ' '.join(command))
    subprocess.run(command, check=True, cwd=ROOT_DIR)


def build_executable(name: str, entry_script: str, icon_path: Path, data_files: list[tuple[Path, str]]) -> None:
    add_data_args: list[str] = []
    for source_path, destination in data_files:
        add_data_args.extend(['--add-data', f'{source_path}{os.pathsep}{destination}'])

    command = [
        sys.executable,
        '-m',
        'PyInstaller',
        '--noconfirm',
        '--clean',
        '--onefile',
        '--windowed',
        '--name',
        name,
        '--distpath',
        str(RAWLOGS_DIR),
        '--workpath',
        str(WORK_DIR / name),
        '--specpath',
        str(SPEC_DIR),
        '--icon',
        str(icon_path),
        *add_data_args,
        entry_script,
    ]
    run_pyinstaller(command)


def copy_runtime_config(source: Path, destination_dir: Path) -> None:
    ensure_dir(destination_dir)
    shutil.copy2(source, destination_dir / source.name)


def main() -> None:
    ensure_dir(RAWLOGS_DIR)
    ensure_dir(WORK_DIR)
    ensure_dir(SPEC_DIR)

    icons = generate_icons()

    shared_icon_dir = 'build_assets/icons'
    build_executable(
        name='Solo_PIXI_Log_Splitter',
        entry_script='log_splitter_app.py',
        icon_path=icons['splitter'],
        data_files=[(icons['splitter'], shared_icon_dir)],
    )
    uploader_data_files = [
        (icons['uploader'], shared_icon_dir),
        (ROOT_DIR / 'solo-pixi-essential' / 'module_log_parser.py', 'solo-pixi-essential'),
    ]
    tn_logo_path = ROOT_DIR / 'build_assets' / 'icons' / 'tn_log.png'
    if tn_logo_path.exists():
        uploader_data_files.append((tn_logo_path, shared_icon_dir))

    build_executable(
        name='Solo_PIXI_Log_Uploader',
        entry_script='log_uploader_app.py',
        icon_path=icons['uploader'],
        data_files=uploader_data_files,
    )
    copy_runtime_config(ROOT_DIR / 'dbservip.conf', RAWLOGS_DIR)

    print('Build completed successfully.')
    print(f'Artifacts: {RAWLOGS_DIR}')


if __name__ == '__main__':
    main()