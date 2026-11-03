"""Verified article-to-model labels for the supported RTU families.

Sources and validation status are in docs/compatibility.md. This is display
metadata only: register layout and device capabilities govern functionality.
Do not infer an unlisted model or a suffix variant from a numeric prefix.
"""

from __future__ import annotations

from typing import Final

ARTICLE_MODELS: Final = {
    "1313207205": "Amtron 4You 310 7.4 C2",
    "1313207205BK": "Amtron 4You 310 7.4 C2",
    "1313201205": "Amtron 4You 310 11 C2",
    "1313201205BK": "Amtron 4You 310 11 C2",
    "1313201605": "Amtron 4You 310 11 T2S",
    "1313201605BK": "Amtron 4You 310 11 T2S",
    "1313202205": "Amtron 4You 310 22 C2",
    "1313202205BK": "Amtron 4You 310 22 C2",
    "1313201265": "Amtron 4You 310 11 C2 PV-Bundle",
    "1313201265BK": "Amtron 4You 310 11 C2 PV-Bundle",
    "1321307205": "Amtron Compact 2.0s 7.4 C2",
    "1321301205": "Amtron Compact 2.0s 11 C2",
    "1321302205": "Amtron Compact 2.0s 22 C2",
    "1321301265": "Amtron Compact 2.0s 11 C2 PV-Bundle",
    "1331307405": "Amtron Start 2.0s 7.4",
    "1331307605": "Amtron Start 2.0s 7.4 T2S",
    "1331302405": "Amtron Start 2.0s 22",
    "1331302605": "Amtron Start 2.0s 22 T2S",
}
