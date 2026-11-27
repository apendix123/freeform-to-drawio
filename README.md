# Freeform → draw.io

Experimental local converter from Apple Freeform's SQLite storage to editable `.drawio` diagrams. It exports all boards or a selected board, including text, native Bézier contours, connector contours and embedded images.

The converter first creates a consistent SQLite backup and copies the assets. It never writes to the original Freeform database, uses no clipboard automation and makes no network requests. Generated diagrams contain your board content and embedded images: keep your exports private unless you intend to share them.

## Quick start

Python 3.10 or newer. No third-party Python packages are required.

```sh
python3 -m freeform_to_drawio --output exports/my-boards
```

Run this from a clone of the repository on the Mac containing the boards. Close Freeform first for a stable asset copy. The live database is opened read-only; SQLite's backup API includes committed WAL data. SQLite may update its shared-memory coordination file when opening a WAL database, but the converter does not issue source writes. Asset files are copied separately and are not part of the SQLite transaction.

Default source:

```text
~/Library/Containers/com.apple.freeform/Data/Library/Freeform/Boards
```

If macOS denies access, allow your terminal to access the Freeform storage in the applicable macOS privacy settings, or use a separately copied store.

Use an existing copy on any supported platform:

```sh
python3 -m freeform_to_drawio --source /path/to/Boards --output exports/imported
```

The output directory must not already exist. Previous exports are never overwritten.

List or select boards:

```sh
python3 -m freeform_to_drawio --output exports/inventory --list
python3 -m freeform_to_drawio --output exports/selected --board 'Board title'
```

`--board` accepts an exact title or UUID. Identical titles select all matches; filenames include full UUIDs to avoid collisions.

Optional installation:

```sh
python3 -m pip install .
freeform-to-drawio --output exports/my-boards
```

## Output

- `diagrams/`: one editable draw.io file per board.
- `all-boards.drawio`: all exported boards as separate pages in one file.
- `index.html`: local list of diagrams and per-board warnings.
- `report.json`: counts, approximations, unsupported objects and decode/export failures.
- `snapshot/`: local backup of the database and assets, retained for recovery and investigation.

Open a `.drawio` file using **File → Open from → Device** in draw.io. The files force light-mode diagram colors and explicit black text, so a dark editor theme does not invert them. Standalone labels preserve explicit line breaks rather than automatically wrapping.

## Fidelity and current limitations

This is an experimental migration tool, not a pixel-perfect Freeform renderer. It reads private Coherence CRDT encoding versions 6 and 7 and has been exercised on a local macOS Freeform store. Future Apple versions may change the schema. The runtime requires neither Rust nor libfreeform; field mappings are adapted from the excellent [libfreeform](https://github.com/can1357/libfreeform) research.

| Feature | Current behavior |
| --- | --- |
| Shapes | Native Bézier paths become editable, resizable draw.io stencils. |
| Text | Editable labels, native dominant font size/style and alignment. Mixed character formatting is simplified. Platform font defaults and shrink-to-fit are approximate. |
| Straight lines | Preserved even when their native frame has zero width or height. |
| Connectors | Editable contours with reconstructed smoothing. They do **not** automatically attach or reroute when a node moves. Arrowheads and exact routing may differ. |
| Groups | Flattened into objects, with ancestral translation/rotation applied. Native layer order, group scaling/flips and complex transforms may differ. |
| Images | Embedded locally; PNG, JPEG, GIF, WebP and SVG. TIFF/HEIC use macOS `sips` for PNG conversion. Crops and masks are approximated by the frame and reported. |
| Parametric shapes | Bounding-rectangle approximation with a warning. Editable Bézier sources are preserved. |
| Sticky notes | Shape and text are exported; shadows are simplified/omitted. |
| Ink, tables, media and unknown kinds | Partial or unsupported. Unsupported item kinds become visible labelled placeholders. Failures and missing assets are recorded in the report. |
| Colors | Basic RGB/gray colors; wide-gamut conversion, alpha, shadows and advanced strokes are approximate or omitted. |

A completed run does not imply every object was faithfully converted. Always inspect `report.json`, especially `failed` and `placeholders`, and compare important boards against Freeform or a PDF export. Keep the source snapshot.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 examples/make_demo.py
```

Tests use synthetic data only: Unicode, malformed protobuf, zero-extent lines, HTML text escaping, embedded images, duplicate board titles, output protection and WAL-aware snapshots. `examples/demo.drawio` is entirely artificial. No personal Freeform captures are included.

MIT license. Adapted schema mappings retain the upstream MIT notice in `THIRD_PARTY_NOTICES`.
