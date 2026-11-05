#!/usr/bin/env python3
"""Copy explicitly mapped sources, name them, and verify a single-root ZIP."""
import argparse
import csv
import hashlib
import json
import os
import shutil
import unicodedata
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZIP_DEFLATED


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def safe_parts(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('package paths must be nonempty POSIX relative paths')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError(f'unsafe path: {value}')
    for part in path.parts:
        if any(ord(c) < 32 or c in '<>:"|?*' for c in part) or part.endswith((' ', '.')):
            raise ValueError(f'nonportable filename: {value}')
        if part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(1, 10)], *[f'LPT{i}' for i in range(1, 10)]}:
            raise ValueError(f'reserved filename: {value}')
    return path


def load_plan(manifest):
    data = json.loads(manifest.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('manifest must be an object')
    if not isinstance(data.get('instructions', ''), str):
        raise ValueError('instructions must be text')
    name = safe_parts(data.get('name'))
    if len(name.parts) != 1:
        raise ValueError('name must be one directory name')
    files = data.get('files')
    if not isinstance(files, list) or not files:
        raise ValueError('files must be a nonempty list')
    used = {unicodedata.normalize('NFC', n).casefold() for n in ('00_阅读说明.txt', '00_文件清单与命名对照.csv')}
    plan = []
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get('source'), str):
            raise ValueError('each file needs source and path')
        rel = safe_parts(entry.get('path'))
        key = unicodedata.normalize('NFC', str(rel)).casefold()
        if key in used or any(key.startswith(k + '/') or k.startswith(key + '/') for k in used):
            raise ValueError(f'colliding destination: {rel}')
        used.add(key)
        if any(p == '__MACOSX' for p in rel.parts) or rel.name == '.DS_Store' or rel.name.startswith('~$'):
            raise ValueError(f'cache or lock file must not be packaged: {rel}')
        src = Path(entry['source']).expanduser()
        src = (src if src.is_absolute() else manifest.parent / src).resolve(strict=True)
        if not src.is_file():
            raise ValueError(f'source is not a file: {src}')
        if src.name == '.DS_Store' or src.name.startswith('~$') or '__MACOSX' in src.parts:
            raise ValueError(f'cache or lock source: {src}')
        plan.append((src, rel, entry))
    return data, str(name), plan


def package(manifest, output_dir, dry_run=False):
    data, name, plan = load_plan(manifest)
    root, archive = output_dir / name, output_dir / (name + '.zip')
    partial = output_dir / (name + '.zip.partial')
    for path in (root, archive, partial):
        if path.exists():
            raise ValueError(f'refusing to overwrite: {path}')
    if dry_run:
        return {'name': name, 'source_files': len(plan), 'destinations': [str(p) for _, p, _ in plan], 'dry_run': True}
    output_dir.mkdir(parents=True, exist_ok=True)
    root.mkdir()
    records = []
    for src, rel, entry in plan:
        dst = root.joinpath(*rel.parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        before = digest(src)
        shutil.copyfile(src, dst)
        os.utime(dst, ns=(src.stat().st_atime_ns, src.stat().st_mtime_ns))
        if digest(dst) != before or digest(src) != before:
            raise ValueError(f'source changed or copy failed: {src}')
        records.append({'类别': entry.get('category', ''), '整理后路径': str(rel), '原文件名': src.name,
                        '原位置或来源': entry.get('source_label', src.name), '文件大小（字节）': dst.stat().st_size,
                        'SHA256': before, '备注': entry.get('note', '')})
    with (root / '00_文件清单与命名对照.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    instructions = data.get('instructions', '')
    if not isinstance(instructions, str):
        raise ValueError('instructions must be text')
    (root / '00_阅读说明.txt').write_text(
        name + '\n\n' + instructions + '\n\n文件清单包含原名称、整理后路径及SHA256。材料按原内容复制，文件名及目录按归档清单调整。\n',
        encoding='utf-8-sig')
    files = sorted(p for p in root.rglob('*') if p.is_file())
    with ZipFile(partial, 'w', compression=ZIP_DEFLATED, compresslevel=6) as zipout:
        for path in files:
            zipout.write(path, str(PurePosixPath(name) / path.relative_to(root).as_posix()))
    with ZipFile(partial) as zipin:
        if zipin.testzip() is not None or len(zipin.infolist()) != len(files):
            raise ValueError('ZIP integrity or file count check failed')
        for path in files:
            member = str(PurePosixPath(name) / path.relative_to(root).as_posix())
            if hashlib.sha256(zipin.read(member)).hexdigest() != digest(path):
                raise ValueError(f'ZIP content mismatch: {member}')
    partial.rename(archive)
    return {'archive': str(archive), 'folder': str(root), 'source_files': len(plan), 'total_files': len(files),
            'archive_bytes': archive.stat().st_size, 'sha256': digest(archive)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        result = package(args.manifest.resolve(), args.output_dir.expanduser().resolve(), args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
