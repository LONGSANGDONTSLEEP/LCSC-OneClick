"""Batch planning and new-library publication for the KiCad target."""
from pathlib import Path

from app_logging import log_event, record_error
from errors import Cancelled
from kicad import FORMATS, FORMAT_LABELS, build_library, conversion_warnings
from library_files import commit_library


def export_batch(results, entries, options, check):
    members = [r for r in results if r.part in entries]
    merged = options.kicad_merge_symbols or options.kicad_merge_footprints
    groups = [members] if merged else [[r] for r in members]
    for rows in groups:
        if not rows:
            continue
        formats = {r.part: tuple(f for f in r.pending_formats if f in FORMATS) for r in rows}
        root = options.output_root() if merged else entries[rows[0].part][1]
        name = options.kicad_library_name
        def libraries(combine):
            return {r.part: name if combine else
                (Path(entries[r.part][1]).relative_to(root) / (name + '_' + r.part)).as_posix()
                for r in rows}
        errors = {}
        try:
            check()
            artifacts, errors = build_library([entries[r.part][0] for r in rows], formats, name, check,
                electrical_policy=options.kicad_electrical_policy,
                symbol_libraries=libraries(options.kicad_merge_symbols),
                footprint_libraries=libraries(options.kicad_merge_footprints))
            written = commit_library(root, artifacts, check)
            for artifact, path in written:
                for row in rows:
                    if row.part in artifact.parts:
                        row.files.append(path)
                        if artifact.format not in row.completed_formats:
                            row.completed_formats.append(artifact.format)
                        row.folder = str(Path(root))
                log_event('INFO', 'download.library_committed', format=artifact.format, mode='new',
                          count=len(artifact.parts), bytes=len(artifact.payload))
            for row in rows:
                done = [f for f in FORMATS if f in row.completed_formats]
                if done:
                    row.message = '；'.join(filter(None, (row.message, '已导出 ' + ' / '.join(FORMAT_LABELS[f] for f in done))))
                    warnings = conversion_warnings(entries[row.part][0], done)
                    if warnings:
                        row.message += '；KiCad：' + '，'.join(warnings)
                        log_event('WARNING', 'export.approximation', part=row.part, target='kicad',
                                  warnings=list(warnings))
        except Exception as exc:
            if not isinstance(exc, Cancelled):
                record_error(exc, 'download.kicad_failed', stage='build_or_commit')
            message = '导出已取消' if isinstance(exc, Cancelled) else str(exc)
            for row in rows:
                for fmt in formats[row.part]:
                    errors.setdefault((row.part, fmt), message)
        for row in rows:
            for fmt in formats[row.part]:
                if (row.part, fmt) in errors:
                    message = FORMAT_LABELS[fmt] + '：' + errors[(row.part, fmt)]
                    row.export_errors.append(message)
                    row.message = '；'.join(filter(None, (row.message, message)))
