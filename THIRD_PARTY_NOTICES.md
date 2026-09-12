# Third-party notices

## PAPP C

This project includes and modifies the PAPP tournament pairing program. The upstream source credits Thierry Bousch as its author; `src/makefile` also credits Emmanuel Lazard and Stephane Nicolet, while `src/main.c` identifies Emmanuel Lazard as the suggestions contact. The upstream PAPP source distribution is licensed under the GNU General Public License version 2 or (at your option) any later version. This repository distributes its PAPP-derived program under GPL version 3.0.

- Upstream project page: <https://www.ffothello.org/informatique/programme-dappariements-papp/>
- Upstream COPYING: <https://www.ffothello.org/papp/doc/COPYING.txt>

## Egaroucid

The bundled Egaroucid for Console engine in `vendor/engines/Egaroucid_for_Console_7_8_1_Windows_AVX512_AMD/` is by Takuto Yamana (Nyanyan). Its accompanying GPL version 3 license and upstream notice are retained in that directory.

- Upstream source: <https://github.com/Nyanyan/Egaroucid>

The bundled `msvcp140.dll` is a Microsoft Visual C++ Runtime component and is omitted from this public repository because it has separate redistribution terms. Install the official Visual C++ Redistributable if it is not already available on the target machine. See [Microsoft's redistribution guidance](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution) and [supported downloads](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist).

## Other bundled components

`@napi-rs/canvas` and `@napi-rs/canvas-win32-x64-msvc` version 1.0.9 are MIT-licensed; their package metadata and license file are retained under `third_party/ap-runtime/`.

`third_party/checkin-frontend/vendor/html2canvas.min.js` is an offline shim by lynweklm, distributed under the MIT License. Its copyright and license notice are retained in the file.
